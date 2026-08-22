"""各ソースを集めて Briefing を1つ組み立てる。"""

from __future__ import annotations

from datetime import date, datetime

from .config import Config
from .models import Briefing
from .sources import calendar_ics, money as money_source, tasks as tasks_source

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def today_in(config: Config) -> date:
    return datetime.now(config.tz).date()


def build_briefing(config: Config, day: date | None = None) -> Briefing:
    tz = config.tz
    target = day or today_in(config)

    calendar_config = config.calendar
    urls = [u for u in (calendar_config.get("ics_urls") or []) if u]
    events, warnings = calendar_ics.collect_events(urls, target, tz)
    if not urls:
        warnings.append("カレンダーのICS URLが未設定です（CALENDAR_ICS_URL）")

    work_start, work_end = config.work_hours
    free_slots = calendar_ics.find_free_slots(
        events,
        target,
        tz,
        work_start,
        work_end,
        int(calendar_config.get("free_slot_min_minutes") or 45),
    )

    tasks_config = config.tasks
    all_tasks = tasks_source.load_tasks(tasks_config.get("file") or "data/tasks.yaml")
    buckets = tasks_source.triage(
        all_tasks, target, int(tasks_config.get("upcoming_days") or 3)
    )

    return Briefing(
        today=target,
        weekday_ja=WEEKDAY_JA[target.weekday()],
        events=events,
        free_slots=free_slots,
        tasks_today=buckets["today"],
        tasks_overdue=buckets["overdue"],
        tasks_upcoming=buckets["upcoming"],
        money=money_source.build_report(config.money, target),
        notes=warnings,
    )
