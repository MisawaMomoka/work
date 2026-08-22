"""ブリーフィングを組み立てるためのデータ構造。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time


@dataclass
class Event:
    summary: str
    start: datetime | None      # 終日予定は None
    end: datetime | None
    all_day: bool = False
    location: str = ""
    calendar: str = ""

    @property
    def time_label(self) -> str:
        if self.all_day or self.start is None:
            return "終日"
        if self.end is None:
            return self.start.strftime("%H:%M")
        return f"{self.start:%H:%M}–{self.end:%H:%M}"

    def sort_key(self) -> tuple[int, time]:
        if self.all_day or self.start is None:
            return (0, time.min)
        return (1, self.start.timetz().replace(tzinfo=None))


@dataclass
class FreeSlot:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    @property
    def label(self) -> str:
        return f"{self.start:%H:%M}–{self.end:%H:%M}（{self.minutes}分）"


@dataclass
class Task:
    title: str
    due: date | None = None
    priority: str = "normal"     # high / normal / low
    project: str = ""
    note: str = ""
    repeat: str = ""             # daily / weekdays / weekly:mon / monthly:15
    done: bool = False
    source_index: int | None = None

    def days_left(self, today: date) -> int | None:
        return None if self.due is None else (self.due - today).days


@dataclass
class UpcomingPayment:
    name: str
    amount: int
    due: date

    def days_left(self, today: date) -> int:
        return (self.due - today).days


@dataclass
class MoneyReport:
    currency: str = "円"
    month_label: str = ""
    spent_this_month: int = 0
    budget: int = 0
    remaining: int = 0
    days_left_in_month: int = 0
    daily_allowance: int = 0
    pace_ratio: float = 0.0          # 1.0 = 予算どおり、>1.0 = 使いすぎ
    spent_yesterday: int = 0
    by_category: list[tuple[str, int, int]] = field(default_factory=list)  # 名前, 使用額, 予算(0=なし)
    over_budget_categories: list[str] = field(default_factory=list)
    fixed_total: int = 0
    upcoming_payments: list[UpcomingPayment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Briefing:
    today: date
    weekday_ja: str
    events: list[Event] = field(default_factory=list)
    free_slots: list[FreeSlot] = field(default_factory=list)
    tasks_today: list[Task] = field(default_factory=list)
    tasks_overdue: list[Task] = field(default_factory=list)
    tasks_upcoming: list[Task] = field(default_factory=list)
    money: MoneyReport = field(default_factory=MoneyReport)
    notes: list[str] = field(default_factory=list)
