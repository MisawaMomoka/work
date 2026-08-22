"""家計の集計。

支出は data/expenses.csv（date,category,amount,memo）に1行ずつ足していく。
固定費と月予算は config.yaml 側に書く。銀行連携は不要で、
「今月あといくら使えるか」「次の引き落としはいつか」が毎朝わかる形にしている。
"""

from __future__ import annotations

import calendar
import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from ..models import MoneyReport, UpcomingPayment

FIELDNAMES = ["date", "category", "amount", "memo"]


class Expense:
    __slots__ = ("date", "category", "amount", "memo")

    def __init__(self, day: date, category: str, amount: int, memo: str = ""):
        self.date = day
        self.category = category or "その他"
        self.amount = amount
        self.memo = memo

    def as_row(self) -> dict[str, str]:
        return {
            "date": self.date.isoformat(),
            "category": self.category,
            "amount": str(self.amount),
            "memo": self.memo,
        }


def _parse_amount(text: str) -> int:
    cleaned = str(text).strip().replace(",", "").replace("円", "").replace("¥", "")
    if not cleaned:
        raise ValueError("金額が空です")
    return int(round(float(cleaned)))


def load_expenses(path: str | Path) -> list[Expense]:
    file = Path(path)
    if not file.is_file():
        return []
    expenses: list[Expense] = []
    with file.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_date = (row.get("date") or "").strip()
            if not raw_date:
                continue
            try:
                expenses.append(
                    Expense(
                        date.fromisoformat(raw_date),
                        (row.get("category") or "").strip(),
                        _parse_amount(row.get("amount") or "0"),
                        (row.get("memo") or "").strip(),
                    )
                )
            except ValueError:
                continue  # 壊れた行は黙って飛ばす。朝の通知を止めない方が大事。
    return expenses


def append_expense(path: str | Path, expense: Expense) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    is_new = not file.is_file() or file.stat().st_size == 0
    with file.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerow(expense.as_row())


def _clamp_day(year: int, month: int, day: int) -> date:
    """31日指定で30日までしかない月なら、その月の末日に寄せる。"""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def upcoming_payments(
    fixed_costs: Iterable[dict[str, Any]],
    today: date,
    within_days: int = 3,
) -> list[UpcomingPayment]:
    """今日から within_days 日以内に引き落とされる固定費。"""
    hits: list[UpcomingPayment] = []
    for item in fixed_costs or []:
        day = int(item.get("day") or 0)
        if not 1 <= day <= 31:
            continue
        amount = int(item.get("amount") or 0)
        name = str(item.get("name") or "固定費")
        candidates = [_clamp_day(today.year, today.month, day)]
        next_month = today.month % 12 + 1
        next_year = today.year + (1 if next_month == 1 else 0)
        candidates.append(_clamp_day(next_year, next_month, day))
        for due in candidates:
            delta = (due - today).days
            if 0 <= delta <= within_days:
                hits.append(UpcomingPayment(name, amount, due))
                break
    hits.sort(key=lambda p: (p.due, -p.amount))
    return hits


def build_report(money_config: dict[str, Any], today: date) -> MoneyReport:
    currency = str(money_config.get("currency") or "円")
    budget = int(money_config.get("monthly_budget") or 0)
    category_budgets: dict[str, int] = {
        str(k): int(v) for k, v in (money_config.get("category_budgets") or {}).items()
    }
    fixed_costs = money_config.get("fixed_costs") or []
    remind_days = int(money_config.get("remind_days_before") or 3)

    expenses = load_expenses(money_config.get("expenses_file") or "data/expenses.csv")
    this_month = [e for e in expenses if (e.date.year, e.date.month) == (today.year, today.month)]

    spent = sum(e.amount for e in this_month)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_left = days_in_month - today.day + 1          # 今日を含む残り日数
    remaining = budget - spent

    # ペース: 「今日までに使っていい額」に対して実際いくら使ったか
    expected_by_now = budget * (today.day / days_in_month) if budget else 0
    pace = (spent / expected_by_now) if expected_by_now > 0 else 0.0

    totals: dict[str, int] = {}
    for expense in this_month:
        totals[expense.category] = totals.get(expense.category, 0) + expense.amount

    by_category: list[tuple[str, int, int]] = []
    for name in sorted(set(totals) | set(category_budgets), key=lambda n: -totals.get(n, 0)):
        by_category.append((name, totals.get(name, 0), category_budgets.get(name, 0)))

    over = [
        name for name, used, cat_budget in by_category
        if cat_budget > 0 and used > cat_budget
    ]

    warnings: list[str] = []
    if budget and remaining < 0:
        warnings.append(f"今月の予算を {abs(remaining):,}{currency} 超過しています")
    elif budget and pace > 1.15:
        warnings.append(f"支出ペースが予定より約{round((pace - 1) * 100)}%速いです")
    for name in over:
        warnings.append(f"「{name}」がカテゴリ予算を超えています")

    return MoneyReport(
        currency=currency,
        month_label=f"{today.year}年{today.month}月",
        spent_this_month=spent,
        budget=budget,
        remaining=remaining,
        days_left_in_month=days_left,
        daily_allowance=max(remaining, 0) // days_left if days_left > 0 else 0,
        pace_ratio=round(pace, 2),
        spent_yesterday=sum(e.amount for e in expenses if (today - e.date).days == 1),
        by_category=by_category,
        over_budget_categories=over,
        fixed_total=sum(int(c.get("amount") or 0) for c in fixed_costs),
        upcoming_payments=upcoming_payments(fixed_costs, today, remind_days),
        warnings=warnings,
    )


def parse_expense_arg(text: str, today: date) -> Expense:
    """`食費 1200 ランチ` / `2026-08-20 食費 1200 ランチ` を Expense にする。"""
    parts = [p for p in text.replace("\t", " ").split(" ") if p]
    if len(parts) < 2:
        raise ValueError("形式: [日付] カテゴリ 金額 [メモ]")
    day = today
    try:
        day = datetime.strptime(parts[0], "%Y-%m-%d").date()
        parts = parts[1:]
    except ValueError:
        pass
    if len(parts) < 2:
        raise ValueError("形式: [日付] カテゴリ 金額 [メモ]")
    return Expense(day, parts[0], _parse_amount(parts[1]), " ".join(parts[2:]))
