"""iCalendar (ICS) フィードから「今日の予定」を取り出す。

Googleカレンダーの「非公開URL(iCal形式)」、Outlook / iCloud の公開ICS、
ローカルの .ics ファイルパスのいずれにも対応する。OAuth設定は不要。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dateutil.rrule import rrulestr
from icalendar import Calendar

from ..models import Event, FreeSlot

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 30

# 貼り間違いが多いので、症状ごとに直し方を返す
STATUS_HINTS = {
    404: "URLが違います。Googleカレンダー →「設定と共有」→「カレンダーの統合」→"
         "「カレンダーの非公開URL（iCal形式）」をコピーし直してください。",
    401: "URLに認証が必要です。非公開URL（iCal形式）をそのまま使ってください。",
    403: "アクセスを拒否されました。カレンダーの共有設定を確認してください。",
}


class CalendarError(RuntimeError):
    pass


def fetch_ics(url: str) -> str:
    """URL または ローカルパスから ICS 本文を取得する。"""
    if url.startswith("webcal://"):
        return fetch_ics("https://" + url[len("webcal://"):])

    if not url.startswith(("http://", "https://")):
        path = Path(url)
        if not path.is_file():
            raise CalendarError(f"ファイルが見つかりません: {url}")
        return _validate_ics(path.read_text(encoding="utf-8"), url)

    try:
        response = requests.get(url, timeout=FETCH_TIMEOUT)
    except requests.RequestException as exc:
        raise CalendarError(f"接続できませんでした: {type(exc).__name__}") from exc

    if response.status_code >= 400:
        hint = STATUS_HINTS.get(response.status_code)
        raise CalendarError(
            f"HTTP {response.status_code} が返りました" + (f"\n  → {hint}" if hint else "")
        )
    return _validate_ics(response.text, url)


def _validate_ics(text: str, url: str) -> str:
    """カレンダー本文でなければ、その場で原因を伝える。"""
    if "BEGIN:VCALENDAR" in text[:2000]:
        return text
    looks_like_html = text.lstrip()[:200].lower().startswith(("<!doctype", "<html"))
    hint = (
        "設定ページのURLや共有用のURLを貼っている可能性があります。"
        "末尾が .ics のURL（カレンダーの非公開URL（iCal形式））を使ってください。"
        if looks_like_html
        else "このURLはカレンダー形式（iCal）ではないようです。"
    )
    raise CalendarError(f"カレンダーとして読めませんでした\n  → {hint}")


def _as_aware(value, tz: ZoneInfo) -> datetime:
    """date / naive datetime / aware datetime を、指定タイムゾーンの aware にそろえる。"""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    return datetime.combine(value, time.min, tzinfo=tz)


def _is_all_day(component) -> bool:
    start = component.get("DTSTART")
    return start is not None and not isinstance(start.dt, datetime)


def _event_duration(component, tz: ZoneInfo) -> timedelta:
    start = component.get("DTSTART")
    end = component.get("DTEND")
    if start is None:
        return timedelta(0)
    if end is not None:
        return _as_aware(end.dt, tz) - _as_aware(start.dt, tz)
    duration = component.get("DURATION")
    if duration is not None:
        return duration.dt
    return timedelta(days=1) if _is_all_day(component) else timedelta(hours=1)


def _exdates(component, tz: ZoneInfo) -> set[datetime]:
    raw = component.get("EXDATE")
    if raw is None:
        return set()
    groups = raw if isinstance(raw, list) else [raw]
    out: set[datetime] = set()
    for group in groups:
        for item in getattr(group, "dts", []):
            out.add(_as_aware(item.dt, tz))
    return out


def _occurrences_on(component, day: date, tz: ZoneInfo) -> list[datetime]:
    """その日に発生する開始日時（複数あり得る）を返す。"""
    dtstart_prop = component.get("DTSTART")
    if dtstart_prop is None:
        return []
    start = _as_aware(dtstart_prop.dt, tz)
    window_start = datetime.combine(day, time.min, tzinfo=tz)
    window_end = window_start + timedelta(days=1)
    duration = _event_duration(component, tz)

    rrule_prop = component.get("RRULE")
    if rrule_prop is None:
        # 単発。日をまたぐ予定も拾えるよう、重なりで判定する。
        return [start] if start < window_end and start + duration > window_start else []

    # 繰り返し。開始が窓より前でも重なるケースを拾うため、少し前から展開する。
    rule = rrulestr(rrule_prop.to_ical().decode("utf-8"), dtstart=start)
    excluded = _exdates(component, tz)
    search_from = window_start - max(duration, timedelta(days=1))
    hits = []
    for occurrence in rule.between(search_from, window_end, inc=True):
        occurrence = _as_aware(occurrence, tz)
        if occurrence in excluded:
            continue
        if occurrence < window_end and occurrence + duration > window_start:
            hits.append(occurrence)
    return hits


def events_for_day(ics_text: str, day: date, tz: ZoneInfo, label: str = "") -> list[Event]:
    calendar = Calendar.from_ical(ics_text)
    calendar_name = label or str(calendar.get("X-WR-CALNAME") or "")
    events: list[Event] = []

    for component in calendar.walk("VEVENT"):
        if str(component.get("STATUS") or "").upper() == "CANCELLED":
            continue
        duration = _event_duration(component, tz)
        all_day = _is_all_day(component)
        for start in _occurrences_on(component, day, tz):
            events.append(
                Event(
                    summary=str(component.get("SUMMARY") or "(タイトルなし)"),
                    start=None if all_day else start,
                    end=None if all_day else start + duration,
                    all_day=all_day,
                    location=str(component.get("LOCATION") or ""),
                    calendar=calendar_name,
                )
            )
    events.sort(key=Event.sort_key)
    return events


def collect_events(urls: list[str], day: date, tz: ZoneInfo) -> tuple[list[Event], list[str]]:
    """複数のICSをまとめて読む。失敗したフィードは警告にして処理は続行する。"""
    events: list[Event] = []
    warnings: list[str] = []
    for url in urls:
        if not url:
            continue
        try:
            events.extend(events_for_day(fetch_ics(url), day, tz))
        except Exception as exc:  # フィード1本のせいで朝の通知が止まらないように
            logger.warning("カレンダーの取得に失敗: %s", exc)
            warnings.append(f"カレンダーを1件読み込めませんでした（{type(exc).__name__}）")
    events.sort(key=Event.sort_key)
    return events, warnings


def find_free_slots(
    events: list[Event],
    day: date,
    tz: ZoneInfo,
    work_start: time,
    work_end: time,
    min_minutes: int = 45,
) -> list[FreeSlot]:
    """勤務時間内の、予定が入っていないまとまった時間を求める。"""
    cursor = datetime.combine(day, work_start, tzinfo=tz)
    day_end = datetime.combine(day, work_end, tzinfo=tz)
    slots: list[FreeSlot] = []

    timed = sorted(
        (e for e in events if not e.all_day and e.start and e.end),
        key=lambda e: e.start,
    )
    for event in timed:
        start = max(event.start, cursor)
        if start > cursor and (start - cursor) >= timedelta(minutes=min_minutes):
            slots.append(FreeSlot(cursor, min(start, day_end)))
        cursor = max(cursor, min(event.end, day_end))
        if cursor >= day_end:
            break

    if day_end - cursor >= timedelta(minutes=min_minutes):
        slots.append(FreeSlot(cursor, day_end))
    return [s for s in slots if s.minutes >= min_minutes]
