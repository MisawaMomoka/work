from datetime import date, time
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
