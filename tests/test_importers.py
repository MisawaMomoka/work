"""他アプリのCSV取り込みのテスト。列名も文字コードも符号もバラバラな前提。"""

from datetime import date

import pytest

from secretary.sources.importers import (
    ImportError_,
    build_plan,
    detect_columns,
    parse_amount,
    parse_date,
    read_text,
)
from secretary.sources.money import Expense


def write(path, text, encoding="utf-8"):
    path.write_text(text, encoding=encoding)
    return path


# ---------------------------------------------------------------- 部品
def test_parse_amount_handles_japanese_formats():
    assert parse_amount("1,234") == 1234
    assert parse_amount("¥8,200") == 8200
    assert parse_amount("1234円") == 1234
    assert parse_amount("-680") == -680
    assert parse_amount("(1,234)") == -1234        # 会計形式
    assert parse_amount("１２３４") == 1234          # 全角
    assert parse_amount("") is None
    assert parse_amount("金額") is None


def test_parse_date_handles_japanese_formats():
    assert parse_date("2026/08/22") == date(2026, 8, 22)
    assert parse_date("2026-08-22") == date(2026, 8, 22)
    assert parse_date("2026年8月22日") == date(2026, 8, 22)
    assert parse_date("20260822") == date(2026, 8, 22)
    assert parse_date("2026/08/22 13:45") == date(2026, 8, 22)
    assert parse_date("2026-08") == date(2026, 8, 1)   # 月次データは1日に寄せる
    assert parse_date("") is None
    assert parse_date("きのう") is None


def test_detect_columns_matches_synonyms():
    mapping = detect_columns(["日付", "収支", "大項目", "中項目", "金額（円）", "メモ"])
    assert mapping["date"] == "日付"
    assert mapping["amount"] == "金額（円）"      # 部分一致
    assert mapping["category"] == "大項目"
    assert mapping["kind"] == "収支"


def test_read_text_detects_shift_jis(tmp_path):
    path = tmp_path / "sjis.csv"
    path.write_bytes("日付,金額\n2026/08/01,1000\n".encode("cp932"))
    text, encoding = read_text(path)
    assert encoding == "cp932"
    assert "日付" in text


def test_read_text_strips_utf8_bom(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbf" + "日付,金額\n2026/08/01,1000\n".encode())
    text, encoding = read_text(path)
    assert encoding == "utf-8-sig"
    assert text.startswith("日付")


# ---------------------------------------------------------------- 取り込み
def test_osidori_style_uses_the_kind_column(tmp_path):
    path = tmp_path / "osidori.csv"
    path.write_bytes(
        (
            "日付,収支,カテゴリ,中項目,金額,メモ\n"
            "2026/08/02,支出,食費,スーパー,\"8,200\",まとめ買い\n"
            "2026/08/10,収入,給与,,\"280,000\",8月分\n"
            "2026/08/12,振替,振替,口座間移動,50000,貯金へ\n"
        ).encode("cp932")
    )
    plan = build_plan(path)

    assert plan.encoding == "cp932"
    assert plan.sign_rule == "収支列で判定"
    assert [e.amount for e in plan.expenses] == [8200]
    assert plan.expenses[0].category == "食費"
    assert plan.expenses[0].memo == "スーパー / まとめ買い"
    assert plan.skipped_income == 1
    assert plan.skipped_transfer == 1


def test_negative_amount_style_skips_income(tmp_path):
    path = write(
        tmp_path / "mf.csv",
        "日付,内容,金額（円）,大項目\n"
        "2026/08/03,セブン,-680,食費\n"
        "2026/08/09,給与,280000,収入\n",
    )
    plan = build_plan(path)
    assert plan.sign_rule == "マイナスの行を支出として取り込み"
    assert [e.amount for e in plan.expenses] == [680]
    assert plan.skipped_income == 1


def test_all_positive_style_treats_everything_as_expense(tmp_path):
    path = write(
        tmp_path / "zaim.csv",
        "日付,カテゴリ,支出,メモ\n2026-08-04,食費,2500,野菜\n2026-08-07,趣味,1800,本\n",
    )
    plan = build_plan(path)
    assert plan.sign_rule == "全行を支出として取り込み"
    assert [e.amount for e in plan.expenses] == [2500, 1800]
    assert plan.total == 4300


def test_existing_rows_are_not_imported_twice(tmp_path):
    path = write(tmp_path / "a.csv", "日付,カテゴリ,金額\n2026-08-04,食費,2500\n")
    existing = [Expense(date(2026, 8, 4), "食費", 2500, "")]

    plan = build_plan(path, existing=existing)
    assert plan.expenses == []
    assert plan.duplicates == 1


def test_duplicate_rows_within_one_file_are_kept_once(tmp_path):
    path = write(
        tmp_path / "a.csv",
        "日付,カテゴリ,金額,メモ\n2026-08-04,食費,2500,野菜\n2026-08-04,食費,2500,野菜\n",
    )
    plan = build_plan(path)
    assert len(plan.expenses) == 1
    assert plan.duplicates == 1


def test_transfers_can_be_included_on_request(tmp_path):
    path = write(
        tmp_path / "a.csv",
        "日付,カテゴリ,金額\n2026-08-04,食費,2500\n2026-08-05,振替,50000\n",
    )
    assert len(build_plan(path).expenses) == 1
    assert len(build_plan(path, include_transfers=True).expenses) == 2


def test_broken_rows_are_counted_not_fatal(tmp_path):
    path = write(
        tmp_path / "a.csv",
        "日付,カテゴリ,金額\n"
        "2026-08-04,食費,2500\n"
        "こわれた行,食費,2500\n"
        "2026-08-06,食費,ぜんぶ\n"
        "2026-08-07,食費,0\n",
    )
    plan = build_plan(path)
    assert len(plan.expenses) == 1
    assert plan.skipped_broken == 3


def test_column_override_wins_over_detection(tmp_path):
    path = write(
        tmp_path / "a.csv",
        "取引日,大項目,支払金額,利用金額\n2026-08-04,食費,999,2500\n",
    )
    plan = build_plan(path, overrides={"amount": "利用金額"})
    assert plan.expenses[0].amount == 2500


def test_missing_amount_column_explains_what_to_do(tmp_path):
    path = write(tmp_path / "a.csv", "日付,説明ぶん\n2026-08-04,なにか\n")
    with pytest.raises(ImportError_) as exc:
        build_plan(path)
    assert "金額" in str(exc.value)
    assert "--amount-col" in str(exc.value)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(ImportError_, match="見つかりません"):
        build_plan(tmp_path / "nope.csv")


def test_empty_file_is_reported(tmp_path):
    path = write(tmp_path / "empty.csv", "")
    with pytest.raises(ImportError_, match="ヘッダー行"):
        build_plan(path)


def test_apply_plan_writes_rows(tmp_path):
    from secretary.sources.importers import apply_plan
    from secretary.sources.money import load_expenses

    path = write(tmp_path / "a.csv", "日付,カテゴリ,金額\n2026-08-04,食費,2500\n")
    target = tmp_path / "expenses.csv"

    assert apply_plan(build_plan(path), target) == 1
    saved = load_expenses(target)
    assert (saved[0].date, saved[0].category, saved[0].amount) == (date(2026, 8, 4), "食費", 2500)


def test_describe_does_not_crash_on_an_empty_plan(tmp_path):
    from secretary.sources.importers import describe

    path = write(tmp_path / "a.csv", "日付,カテゴリ,金額\n2026-08-05,振替,50000\n")
    text = describe(build_plan(path))
    assert "取り込む件数: 0件" in text
