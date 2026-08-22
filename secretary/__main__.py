"""AI秘書のコマンドラインインターフェース。

    python -m secretary brief              # 今日のブリーフィングを作って送る
    python -m secretary brief --dry-run    # 送らずに画面に出すだけ
    python -m secretary money              # お金の状況だけ見る
    python -m secretary add 食費 1200 ランチ  # 支出を記録
    python -m secretary task list
    python -m secretary task add "請求書を送る" --due 2026-08-25 --priority high
    python -m secretary task done 3
    python -m secretary doctor             # 設定の健康診断
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

from . import brief as brief_module
from . import notify
from .config import Config, ConfigError, load_config
from .models import Task
from .pipeline import build_briefing, today_in
from .sources import money as money_source
from .sources import tasks as tasks_source


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )


def _load(args) -> Config:
    return load_config(args.config)


def _tasks_path(config: Config) -> str:
    return config.tasks.get("file") or "data/tasks.yaml"


# ------------------------------------------------------------------ brief
def cmd_brief(args) -> int:
    config = _load(args)
    target = date.fromisoformat(args.date) if args.date else today_in(config)
    briefing = build_briefing(config, target)

    body = brief_module.render_plain(briefing) if args.no_ai else brief_module.compose(briefing, config)
    subject = f"☀️ {target.year}年{target.month}月{target.day}日（{briefing.weekday_ja}）のブリーフィング"

    if args.dry_run:
        print(body)
        return 0

    delivered = notify.dispatch(config.notify, subject, body)
    if not delivered:
        print(body)
        print("\n[警告] 有効な通知チャネルがありません。config.yaml の notify を確認してください。",
              file=sys.stderr)
        return 1
    logging.info("送信しました: %s", ", ".join(delivered))
    return 0


# ------------------------------------------------------------------ money
def cmd_money(args) -> int:
    config = _load(args)
    report = money_source.build_report(config.money, today_in(config))
    unit = report.currency
    print(f"■ {report.month_label} の家計")
    print(f"  支出        : {report.spent_this_month:,}{unit}")
    if report.budget:
        print(f"  予算        : {report.budget:,}{unit}")
        print(f"  残り        : {report.remaining:,}{unit}（月末まで{report.days_left_in_month}日）")
        print(f"  1日あたり   : {report.daily_allowance:,}{unit} まで")
        print(f"  ペース      : {report.pace_ratio}（1.00が予定どおり）")
    print(f"  固定費合計  : {report.fixed_total:,}{unit}/月")
    if report.by_category:
        print("  カテゴリ別  :")
        for name, used, budget in report.by_category:
            suffix = f" / {budget:,}" if budget else ""
            flag = "  ← 超過" if budget and used > budget else ""
            print(f"    {name:<8} {used:>9,}{suffix}{flag}")
    for payment in report.upcoming_payments:
        print(f"  ⏰ {payment.due} {payment.name} {payment.amount:,}{unit}")
    for warning in report.warnings:
        print(f"  ⚠️  {warning}")
    return 0


def cmd_add(args) -> int:
    config = _load(args)
    path = config.money.get("expenses_file") or "data/expenses.csv"
    try:
        expense = money_source.parse_expense_arg(" ".join(args.words), today_in(config))
    except ValueError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    money_source.append_expense(path, expense)
    print(f"記録しました: {expense.date} {expense.category} {expense.amount:,}"
          f"{config.money.get('currency') or '円'} {expense.memo}".rstrip())
    return 0


# ------------------------------------------------------------------ task
def cmd_task(args) -> int:
    config = _load(args)
    path = _tasks_path(config)
    tasks = tasks_source.load_tasks(path)
    today = today_in(config)

    if args.task_command == "list":
        if not tasks:
            print("タスクはありません。")
            return 0
        for index, task in enumerate(tasks):
            status = "✔" if task.done else " "
            due = f" (期限 {task.due})" if task.due else ""
            repeat = f" [{task.repeat}]" if task.repeat else ""
            priority = " ★" if task.priority == "high" else ""
            print(f"{index:>3} [{status}] {task.title}{due}{repeat}{priority}")
        buckets = tasks_source.triage(tasks, today, int(config.tasks.get("upcoming_days") or 3))
        print(f"\n今日: {len(buckets['today'])}件 / 期限切れ: {len(buckets['overdue'])}件"
              f" / まもなく: {len(buckets['upcoming'])}件")
        return 0

    if args.task_command == "add":
        tasks.append(
            Task(
                title=args.title,
                due=date.fromisoformat(args.due) if args.due else None,
                priority=args.priority,
                project=args.project or "",
                repeat=args.repeat or "",
            )
        )
        tasks_source.save_tasks(path, tasks)
        print(f"追加しました: {args.title}")
        return 0

    if args.task_command == "done":
        if not 0 <= args.index < len(tasks):
            print(f"エラー: 番号 {args.index} のタスクはありません", file=sys.stderr)
            return 2
        tasks[args.index].done = True
        tasks_source.save_tasks(path, tasks)
        print(f"完了にしました: {tasks[args.index].title}")
        return 0

    return 2


# ------------------------------------------------------------------ doctor
def cmd_doctor(args) -> int:
    config = _load(args)
    print(f"設定ファイル : {config.source}")
    print(f"タイムゾーン : {config.tz}  (現在 {datetime.now(config.tz):%Y-%m-%d %H:%M})")

    urls = [u for u in (config.calendar.get("ics_urls") or []) if u]
    print(f"カレンダー   : {len(urls)}件のICSを設定" if urls else "カレンダー   : ✗ 未設定")
    problems = 0
    for url in urls:
        try:
            from .sources.calendar_ics import events_for_day, fetch_ics
            count = len(events_for_day(fetch_ics(url), today_in(config), config.tz))
            print(f"  ✓ 取得OK（今日の予定 {count}件）")
        except Exception as exc:
            problems += 1
            print(f"  ✗ 取得失敗: {type(exc).__name__}: {exc}")

    tasks = tasks_source.load_tasks(_tasks_path(config))
    print(f"タスク       : {len(tasks)}件 ({_tasks_path(config)})")

    expenses_file = config.money.get("expenses_file") or "data/expenses.csv"
    print(f"支出ログ     : {len(money_source.load_expenses(expenses_file))}件 ({expenses_file})")

    enabled = [n for n, s in (config.notify or {}).items() if isinstance(s, dict) and s.get("enabled")]
    print(f"通知チャネル : {', '.join(enabled) if enabled else '✗ なし'}")
    if not enabled:
        problems += 1

    import os
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    print(f"AI整形       : {'✓ APIキーあり' if has_key else '△ キー無し（テンプレート出力で動作）'}")

    print("\n問題なし" if problems == 0 else f"\n{problems}件の要確認あり")
    return 0 if problems == 0 else 1


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="secretary", description="毎朝のブリーフィングを届けるAI秘書")
    parser.add_argument("-c", "--config", help="設定ファイルのパス")
    parser.add_argument("-v", "--verbose", action="store_true", help="詳細ログ")
    sub = parser.add_subparsers(dest="command", required=True)

    p_brief = sub.add_parser("brief", help="ブリーフィングを作って通知する")
    p_brief.add_argument("--dry-run", action="store_true", help="送信せず画面に出すだけ")
    p_brief.add_argument("--no-ai", action="store_true", help="AI整形を使わない")
    p_brief.add_argument("--date", help="対象日 (YYYY-MM-DD)")
    p_brief.set_defaults(func=cmd_brief)

    sub.add_parser("money", help="家計の状況を表示").set_defaults(func=cmd_money)

    p_add = sub.add_parser("add", help="支出を記録: add [日付] カテゴリ 金額 [メモ]")
    p_add.add_argument("words", nargs="+")
    p_add.set_defaults(func=cmd_add)

    p_task = sub.add_parser("task", help="タスクの操作")
    task_sub = p_task.add_subparsers(dest="task_command", required=True)
    task_sub.add_parser("list", help="一覧")
    t_add = task_sub.add_parser("add", help="追加")
    t_add.add_argument("title")
    t_add.add_argument("--due", help="期限 (YYYY-MM-DD)")
    t_add.add_argument("--priority", default="normal", choices=["high", "normal", "low"])
    t_add.add_argument("--project")
    t_add.add_argument("--repeat", help="daily / weekdays / weekly:mon / monthly:15")
    t_done = task_sub.add_parser("done", help="完了にする")
    t_done.add_argument("index", type=int)
    p_task.set_defaults(func=cmd_task)

    sub.add_parser("doctor", help="設定の健康診断").set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
