from datetime import date, datetime
from zoneinfo import ZoneInfo

from secretary.brief import render_plain, to_payload
from secretary.config import Config
from secretary.models import Briefing, Event, FreeSlot, MoneyReport, Task, UpcomingPayment

TZ = ZoneInfo("Asia/Tokyo")


def sample() -> Briefing:
    day = date(2026, 8, 20)
    return Briefing(
        today=day,
        weekday_ja="木",
        events=[
            Event(
                summary="定例MTG",
                start=datetime(2026, 8, 20, 10, 0, tzinfo=TZ),
                end=datetime(2026, 8, 20, 11, 0, tzinfo=TZ),
                location="会議室A",
            )
        ],
        free_slots=[FreeSlot(datetime(2026, 8, 20, 11, 0, tzinfo=TZ),
                             datetime(2026, 8, 20, 13, 0, tzinfo=TZ))],
        tasks_today=[Task(title="請求書を送る", due=day, priority="high")],
        tasks_overdue=[Task(title="経費精算", due=date(2026, 8, 18))],
        tasks_upcoming=[Task(title="健康診断の予約", due=date(2026, 8, 22))],
        money=MoneyReport(
            currency="円",
            month_label="2026年8月",
            spent_this_month=50000,
            budget=120000,
            remaining=70000,
            days_left_in_month=12,
            daily_allowance=5833,
            spent_yesterday=2400,
            upcoming_payments=[UpcomingPayment("家賃", 82000, date(2026, 8, 22))],
            warnings=["「趣味」がカテゴリ予算を超えています"],
        ),
    )


def test_render_plain_contains_every_section():
    text = render_plain(sample())
    for expected in ["📅 予定", "✅ タスク", "💰 お金", "定例MTG", "10:00–11:00", "@会議室A"]:
        assert expected in text


def test_render_plain_marks_overdue_and_priority():
    text = render_plain(sample())
    assert "[期限切れ 2日] 経費精算" in text
    assert "★請求書を送る" in text
    assert "健康診断の予約(8/22)" in text


def test_render_plain_money_numbers_are_formatted():
    text = render_plain(sample())
    assert "50,000円 / 120,000円" in text
    assert "残り12日 → 1日あたり 5,833円 まで" in text
    assert "引き落とし 8/22: 家賃 82,000円" in text
    assert "⚠️ 「趣味」がカテゴリ予算を超えています" in text


def test_render_plain_handles_empty_day():
    empty = Briefing(today=date(2026, 8, 20), weekday_ja="木",
                     money=MoneyReport(month_label="2026年8月"))
    text = render_plain(empty)
    assert "・予定なし" in text
    assert "・今日やるべきタスクはありません" in text


def test_payload_is_json_safe():
    import json

    payload = to_payload(sample())
    json.dumps(payload, ensure_ascii=False)  # 落ちないこと
    assert payload["お金"]["残り予算"] == 70000
    assert payload["期限切れタスク"][0]["何日超過"] == 2


def test_ai_disabled_falls_back_to_template():
    from secretary.brief import compose

    config = Config({"ai": {"enabled": False}, "profile": {}})
    assert compose(sample(), config) == render_plain(sample())


def test_ai_failure_falls_back_to_template(monkeypatch):
    """APIキーが無い / API が落ちている朝でも、通知は必ず出ること。"""
    import anthropic

    from secretary.brief import compose

    def boom(*args, **kwargs):
        raise anthropic.APIConnectionError(request=None)

    monkeypatch.setattr(anthropic, "Anthropic", boom)
    config = Config({"ai": {"enabled": True, "model": "claude-opus-5"}, "profile": {}})
    assert compose(sample(), config) == render_plain(sample())
