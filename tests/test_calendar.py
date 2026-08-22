from datetime import date, time

import pytest
from zoneinfo import ZoneInfo

from secretary.sources.calendar_ics import events_for_day, find_free_slots

TZ = ZoneInfo("Asia/Tokyo")


def ics(*events: str) -> str:
    return "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//JP\n" + "".join(events) + "END:VCALENDAR\n"


TIMED = (
    "BEGIN:VEVENT\nUID:1\nSUMMARY:定例ミーティング\nLOCATION:会議室A\n"
    "DTSTART;TZID=Asia/Tokyo:20260820T100000\n"
    "DTEND;TZID=Asia/Tokyo:20260820T110000\nEND:VEVENT\n"
)
ALL_DAY = (
    "BEGIN:VEVENT\nUID:2\nSUMMARY:有給\n"
    "DTSTART;VALUE=DATE:20260820\nDTEND;VALUE=DATE:20260821\nEND:VEVENT\n"
)
WEEKLY = (
    "BEGIN:VEVENT\nUID:3\nSUMMARY:週次レビュー\n"
    "DTSTART;TZID=Asia/Tokyo:20260803T150000\n"
    "DTEND;TZID=Asia/Tokyo:20260803T160000\n"
    "RRULE:FREQ=WEEKLY;BYDAY=MO\nEND:VEVENT\n"
)
CANCELLED = (
    "BEGIN:VEVENT\nUID:4\nSUMMARY:中止された会\nSTATUS:CANCELLED\n"
    "DTSTART;TZID=Asia/Tokyo:20260820T090000\n"
    "DTEND;TZID=Asia/Tokyo:20260820T093000\nEND:VEVENT\n"
)


def test_timed_event_on_matching_day():
    events = events_for_day(ics(TIMED), date(2026, 8, 20), TZ)
    assert len(events) == 1
    assert events[0].summary == "定例ミーティング"
    assert events[0].time_label == "10:00–11:00"
    assert events[0].location == "会議室A"


def test_event_not_returned_on_other_day():
    assert events_for_day(ics(TIMED), date(2026, 8, 21), TZ) == []


def test_all_day_event():
    events = events_for_day(ics(ALL_DAY), date(2026, 8, 20), TZ)
    assert len(events) == 1
    assert events[0].all_day is True
    assert events[0].time_label == "終日"


def test_all_day_sorts_before_timed():
    events = events_for_day(ics(TIMED, ALL_DAY), date(2026, 8, 20), TZ)
    assert [e.summary for e in events] == ["有給", "定例ミーティング"]


def test_recurring_weekly_expands_to_later_monday():
    events = events_for_day(ics(WEEKLY), date(2026, 8, 24), TZ)  # 月曜
    assert [e.summary for e in events] == ["週次レビュー"]
    assert events_for_day(ics(WEEKLY), date(2026, 8, 25), TZ) == []  # 火曜


def test_cancelled_event_is_skipped():
    assert events_for_day(ics(CANCELLED), date(2026, 8, 20), TZ) == []


def test_free_slots_between_meetings():
    events = events_for_day(ics(TIMED), date(2026, 8, 20), TZ)
    slots = find_free_slots(events, date(2026, 8, 20), TZ, time(9, 0), time(18, 0), 45)
    labels = [s.label for s in slots]
    assert labels == ["09:00–10:00（60分）", "11:00–18:00（420分）"]


def test_short_gaps_are_not_free_slots():
    events = events_for_day(ics(TIMED), date(2026, 8, 20), TZ)
    slots = find_free_slots(events, date(2026, 8, 20), TZ, time(9, 30), time(11, 0), 45)
    assert slots == []


def test_all_day_event_does_not_block_free_time():
    events = events_for_day(ics(ALL_DAY), date(2026, 8, 20), TZ)
    slots = find_free_slots(events, date(2026, 8, 20), TZ, time(9, 0), time(18, 0), 45)
    assert len(slots) == 1 and slots[0].minutes == 540


# ---------------------------------------------------------------- 取得時のエラー
def test_html_page_gives_a_useful_error(tmp_path):
    """公開URLや設定ページのURLを貼ってしまう間違いを、その場で指摘する。"""
    from secretary.sources.calendar_ics import CalendarError, fetch_ics

    page = tmp_path / "page.html"
    page.write_text("<!DOCTYPE html><html><body>Google Calendar</body></html>", encoding="utf-8")

    with pytest.raises(CalendarError, match="basic.ics|iCal形式"):
        fetch_ics(str(page))


def test_non_calendar_text_is_rejected(tmp_path):
    from secretary.sources.calendar_ics import CalendarError, fetch_ics

    junk = tmp_path / "junk.txt"
    junk.write_text("これはカレンダーではありません", encoding="utf-8")

    with pytest.raises(CalendarError, match="カレンダーとして読めませんでした"):
        fetch_ics(str(junk))


def test_missing_local_file_is_reported(tmp_path):
    from secretary.sources.calendar_ics import CalendarError, fetch_ics

    with pytest.raises(CalendarError, match="見つかりません"):
        fetch_ics(str(tmp_path / "nope.ics"))


def test_valid_ics_file_passes_validation(tmp_path):
    from secretary.sources.calendar_ics import fetch_ics

    path = tmp_path / "ok.ics"
    path.write_text(ics(TIMED), encoding="utf-8")
    assert "BEGIN:VCALENDAR" in fetch_ics(str(path))


def test_http_404_suggests_recopying_the_url(monkeypatch):
    import requests

    from secretary.sources.calendar_ics import CalendarError, fetch_ics

    class Resp:
        status_code = 404
        text = "Not Found"

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    with pytest.raises(CalendarError) as exc:
        fetch_ics("https://example.com/missing.ics")
    assert "404" in str(exc.value)
    assert "非公開URL" in str(exc.value)


def test_connection_failure_is_wrapped(monkeypatch):
    import requests

    from secretary.sources.calendar_ics import CalendarError, fetch_ics

    def boom(*a, **k):
        raise requests.ConnectionError("dns failure")

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(CalendarError, match="接続できませんでした"):
        fetch_ics("https://example.com/a.ics")


def test_collect_events_survives_one_broken_feed(tmp_path):
    """1本壊れていても、他のカレンダーと通知自体は生き残ること。"""
    from secretary.sources.calendar_ics import collect_events

    good = tmp_path / "good.ics"
    good.write_text(ics(TIMED), encoding="utf-8")

    events, warnings = collect_events(
        [str(good), str(tmp_path / "missing.ics")], date(2026, 8, 20), TZ
    )
    assert [e.summary for e in events] == ["定例ミーティング"]
    assert len(warnings) == 1


def test_webcal_scheme_is_converted(monkeypatch):
    """iCloud の webcal:// をそのまま貼っても動くこと。"""
    import requests

    from secretary.sources.calendar_ics import fetch_ics

    seen = {}

    class Resp:
        status_code = 200
        text = ics(TIMED)

    def fake_get(url, **kwargs):
        seen["url"] = url
        return Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_ics("webcal://example.com/cal.ics")
    assert seen["url"] == "https://example.com/cal.ics"


# ---------------------------------------------------------------- URLのマスク
def test_mask_url_hides_the_private_token():
    """画面に出すURLから、非公開トークンの断片も残さないこと。"""
    from secretary.__main__ import _mask_url

    secret = "private-aaaabbbbccccddddeeeeffff11112222"
    url = f"https://calendar.google.com/calendar/ical/me%40gmail.com/{secret}/basic.ics"
    masked = _mask_url(url)

    assert "calendar.google.com" in masked      # どのサービスかは分かる
    assert ".ics" in masked                     # 形式も分かる
    assert "private" not in masked
    assert "me%40gmail.com" not in masked
    # トークンの断片が4文字以上残っていないこと
    for start in range(len(secret) - 4):
        assert secret[start:start + 5] not in masked


def test_mask_url_keeps_local_paths_to_the_filename():
    from secretary.__main__ import _mask_url

    assert _mask_url("/home/momoi/secret-folder/work.ics") == "work.ics"


def test_mask_url_handles_urls_without_a_path():
    from secretary.__main__ import _mask_url

    assert _mask_url("https://example.com") == "https://example.com"


def test_mask_url_hides_webcal_tokens():
    """iCloud の webcal:// URL もトークンを含むので伏せること。"""
    from secretary.__main__ import _mask_url

    masked = _mask_url("webcal://p12-caldav.icloud.com/published/2/MTIzNDU2Nzg5SECRET")
    assert "p12-caldav.icloud.com" in masked
    assert "MTIzNDU2Nzg5SECRET" not in masked
    assert "SECRET" not in masked
