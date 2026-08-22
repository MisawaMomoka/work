from datetime import date

from secretary.sources.money import (
    Expense,
    append_expense,
    build_report,
    load_expenses,
    parse_expense_arg,
    upcoming_payments,
)


def test_append_and_load_roundtrip(tmp_path):
    path = tmp_path / "expenses.csv"
    append_expense(path, Expense(date(2026, 8, 1), "食費", 1200, "ランチ"))
    append_expense(path, Expense(date(2026, 8, 2), "交通費", 480, ""))

    loaded = load_expenses(path)
    assert [e.amount for e in loaded] == [1200, 480]
    assert loaded[0].memo == "ランチ"
    assert path.read_text(encoding="utf-8").splitlines()[0] == "date,category,amount,memo"


def test_load_skips_broken_rows(tmp_path):
    path = tmp_path / "expenses.csv"
    path.write_text(
        "date,category,amount,memo\n"
        "2026-08-01,食費,1000,ok\n"
        "not-a-date,食費,500,壊れた行\n"
        "2026-08-02,食費,ぜんぶ,金額が数字でない\n"
        "2026-08-03,食費,\"1,500\",カンマ入り\n",
        encoding="utf-8",
    )
    loaded = load_expenses(path)
    assert [e.amount for e in loaded] == [1000, 1500]


def test_upcoming_payments_clamps_to_month_end():
    # 2026年2月は28日まで。31日指定は月末に寄せる。
    hits = upcoming_payments([{"name": "家賃", "amount": 80000, "day": 31}], date(2026, 2, 26), 3)
    assert [p.due for p in hits] == [date(2026, 2, 28)]


def test_upcoming_payments_rolls_into_next_month():
    hits = upcoming_payments([{"name": "電気", "amount": 7000, "day": 2}], date(2026, 8, 31), 3)
    assert [p.due for p in hits] == [date(2026, 9, 2)]


def test_upcoming_payments_ignores_far_dates():
    assert upcoming_payments([{"name": "家賃", "amount": 1, "day": 27}], date(2026, 8, 1), 3) == []


def test_build_report_budget_math(tmp_path):
    path = tmp_path / "expenses.csv"
    path.write_text(
        "date,category,amount,memo\n"
        "2026-08-01,食費,30000,\n"
        "2026-08-05,趣味,20000,\n"
        "2026-07-30,食費,99999,先月ぶんは含めない\n",
        encoding="utf-8",
    )
    report = build_report(
        {
            "expenses_file": str(path),
            "monthly_budget": 100000,
            "category_budgets": {"趣味": 15000},
            "fixed_costs": [{"name": "家賃", "amount": 80000, "day": 27}],
        },
        date(2026, 8, 16),
    )
    assert report.spent_this_month == 50000
    assert report.remaining == 50000
    assert report.days_left_in_month == 16          # 8/16〜8/31
    assert report.daily_allowance == 50000 // 16
    assert "趣味" in report.over_budget_categories
    assert report.fixed_total == 80000


def test_build_report_flags_overspend(tmp_path):
    path = tmp_path / "expenses.csv"
    path.write_text("date,category,amount,memo\n2026-08-02,食費,120000,\n", encoding="utf-8")
    report = build_report(
        {"expenses_file": str(path), "monthly_budget": 100000}, date(2026, 8, 3)
    )
    assert report.remaining == -20000
    assert any("超過" in w for w in report.warnings)


def test_parse_expense_arg():
    parsed = parse_expense_arg("食費 1,200 ランチ 会議込み", date(2026, 8, 20))
    assert (parsed.date, parsed.category, parsed.amount) == (date(2026, 8, 20), "食費", 1200)
    assert parsed.memo == "ランチ 会議込み"

    dated = parse_expense_arg("2026-08-18 交通費 480", date(2026, 8, 20))
    assert dated.date == date(2026, 8, 18)
    assert dated.amount == 480
