"""ブリーフィングの文章化。

まず素のテンプレートで確実に読める形を作り、AIが使えるときだけ
それを秘書らしい文章に整える。APIキーが無くても壊れないのが前提。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import Config
from .models import Briefing

logger = logging.getLogger(__name__)

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

SYSTEM_PROMPT = """\
あなたはユーザー専属の日本語の秘書です。毎朝、その日のブリーフィングを届けます。

書き方のルール:
- スマホの通知で読むことを前提に、全体で400〜600字程度にまとめる。
- 構成は必ず「今日のひとこと要約 → 予定 → タスク → お金 → 最後に一言」の順。
- 見出しは絵文字1つ＋短い語（例: 📅 予定）。Markdownの見出し記号(#)や太字(**)は使わない。
- 与えられたデータに無いことは書かない。予定やタスクを創作しない。
- 数字（時刻・金額）はデータのとおり正確に。金額は3桁区切り。
- 予定が詰まっている日、支出ペースが速い日など、注意すべき点があれば具体的に指摘する。
- 空き時間があれば、その枠でどのタスクを片づけるか1つだけ提案する。
- 精神論や一般論の励ましは書かない。最後の一言は事実にもとづく短いものにする。
"""


# ---------------------------------------------------------------- 素のテンプレート
def _yen(amount: int, currency: str) -> str:
    return f"{amount:,}{currency}"


def _md(day) -> str:
    """`%-m/%-d` はWindowsで動かないので自前で組む。"""
    return f"{day.month}/{day.day}"


def render_plain(briefing: Briefing) -> str:
    b = briefing
    money = b.money
    header = f"{b.today.year}年{b.today.month}月{b.today.day}日"
    lines: list[str] = [f"☀️ {header}（{b.weekday_ja}）のブリーフィング", ""]

    lines.append("📅 予定")
    if b.events:
        for event in b.events:
            place = f"　@{event.location}" if event.location else ""
            lines.append(f"・{event.time_label}　{event.summary}{place}")
    else:
        lines.append("・予定なし")
    if b.free_slots:
        lines.append("・空き: " + " / ".join(slot.label for slot in b.free_slots[:3]))
    lines.append("")

    lines.append("✅ タスク")
    if b.tasks_overdue:
        for task in b.tasks_overdue:
            lines.append(f"・[期限切れ {abs(task.days_left(b.today))}日] {task.title}")
    if b.tasks_today:
        for task in b.tasks_today:
            mark = "★" if task.priority == "high" else "・"
            lines.append(f"{mark}{task.title}")
    if not b.tasks_overdue and not b.tasks_today:
        lines.append("・今日やるべきタスクはありません")
    if b.tasks_upcoming:
        soon = "、".join(f"{t.title}({_md(t.due)})" for t in b.tasks_upcoming[:4])
        lines.append(f"・まもなく: {soon}")
    lines.append("")

    lines.append("💰 お金")
    currency = money.currency
    if money.budget:
        lines.append(
            f"・{money.month_label}: {_yen(money.spent_this_month, currency)} / "
            f"{_yen(money.budget, currency)}（残り {_yen(money.remaining, currency)}）"
        )
        lines.append(
            f"・残り{money.days_left_in_month}日 → 1日あたり "
            f"{_yen(money.daily_allowance, currency)} まで"
        )
    else:
        lines.append(f"・{money.month_label}の支出: {_yen(money.spent_this_month, currency)}")
    if money.spent_yesterday:
        lines.append(f"・昨日の支出: {_yen(money.spent_yesterday, currency)}")
    for payment in money.upcoming_payments:
        when = "今日" if payment.days_left(b.today) == 0 else _md(payment.due)
        lines.append(f"・引き落とし {when}: {payment.name} {_yen(payment.amount, currency)}")
    for warning in money.warnings:
        lines.append(f"・⚠️ {warning}")

    if b.notes:
        lines.append("")
        for note in b.notes:
            lines.append(f"※ {note}")
    return "\n".join(lines)


# ---------------------------------------------------------------- AI用の入力データ
def to_payload(briefing: Briefing) -> dict[str, Any]:
    b = briefing
    money = b.money
    return {
        "日付": f"{b.today:%Y-%m-%d}",
        "曜日": b.weekday_ja,
        "予定": [
            {"時間": e.time_label, "件名": e.summary, "場所": e.location}
            for e in b.events
        ],
        "空き時間": [s.label for s in b.free_slots],
        "期限切れタスク": [
            {"件名": t.title, "何日超過": abs(t.days_left(b.today) or 0)} for t in b.tasks_overdue
        ],
        "今日のタスク": [
            {"件名": t.title, "優先度": t.priority, "案件": t.project} for t in b.tasks_today
        ],
        "まもなく期限のタスク": [
            {"件名": t.title, "期限": t.due.isoformat() if t.due else None}
            for t in b.tasks_upcoming
        ],
        "お金": {
            "通貨": money.currency,
            "対象月": money.month_label,
            "今月の支出": money.spent_this_month,
            "今月の予算": money.budget,
            "残り予算": money.remaining,
            "月末までの日数": money.days_left_in_month,
            "1日あたり使える額": money.daily_allowance,
            "支出ペース": money.pace_ratio,
            "昨日の支出": money.spent_yesterday,
            "カテゴリ別": [
                {"カテゴリ": name, "支出": used, "予算": cat_budget}
                for name, used, cat_budget in money.by_category
                if used or cat_budget
            ],
            "近い引き落とし": [
                {"名目": p.name, "金額": p.amount, "日付": p.due.isoformat()}
                for p in money.upcoming_payments
            ],
            "注意": money.warnings,
        },
        "補足": b.notes,
    }


# ---------------------------------------------------------------- AIで整える
def generate_with_ai(briefing: Briefing, config: Config) -> str | None:
    ai = config.ai
    if not ai.get("enabled", True):
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic パッケージが無いため、テンプレート出力にします")
        return None

    profile = config.profile
    name = profile.get("name") or ""
    tone = profile.get("tone") or "丁寧で簡潔な、有能な秘書"
    system = SYSTEM_PROMPT + f"\n口調: {tone}\n" + (f"呼びかけ: {name}さん\n" if name else "")

    user_content = (
        "以下は今日ぶんのデータです。これだけを根拠にブリーフィングを書いてください。\n\n"
        + json.dumps(to_payload(briefing), ensure_ascii=False, indent=2)
    )

    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=ai.get("model") or "claude-opus-5",
            max_tokens=4000,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": ai.get("effort") or "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": user_content}],
        )
        if getattr(response, "stop_reason", None) == "refusal":
            logger.warning("モデルが応答を拒否しました。テンプレート出力にします")
            return None
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        return text or None
    except Exception as exc:
        logger.warning("AIでの整形に失敗（%s）。テンプレート出力にします", type(exc).__name__)
        return None


def compose(briefing: Briefing, config: Config) -> str:
    """AIで整えた文章を返す。無理ならテンプレートをそのまま返す。"""
    return generate_with_ai(briefing, config) or render_plain(briefing)
