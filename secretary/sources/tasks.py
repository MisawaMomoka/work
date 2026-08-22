"""タスク一覧（data/tasks.yaml）の読み書きと、今日ぶんの絞り込み。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from ..models import Task

WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


def _parse_due(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def load_tasks(path: str | Path) -> list[Task]:
    file = Path(path)
    if not file.is_file():
        return []
    raw = yaml.safe_load(file.read_text(encoding="utf-8")) or []
    if isinstance(raw, dict):          # {tasks: [...]} 形式も許容
        raw = raw.get("tasks") or []
    tasks: list[Task] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):      # "牛乳を買う" のような素の文字列も許容
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        tasks.append(
            Task(
                title=str(item.get("title") or "").strip(),
                due=_parse_due(item.get("due")),
                priority=str(item.get("priority") or "normal").lower(),
                project=str(item.get("project") or ""),
                note=str(item.get("note") or ""),
                repeat=str(item.get("repeat") or "").lower().strip(),
                done=bool(item.get("done")),
                source_index=index,
            )
        )
    return [t for t in tasks if t.title]


def save_tasks(path: str | Path, tasks: list[Task]) -> None:
    payload = []
    for task in tasks:
        item: dict[str, Any] = {"title": task.title}
        if task.due:
            item["due"] = task.due.isoformat()
        if task.priority and task.priority != "normal":
            item["priority"] = task.priority
        if task.project:
            item["project"] = task.project
        if task.note:
            item["note"] = task.note
        if task.repeat:
            item["repeat"] = task.repeat
        if task.done:
            item["done"] = True
        payload.append(item)
    Path(path).write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def repeats_today(task: Task, today: date) -> bool:
    """repeat 指定（daily / weekdays / weekly:mon / monthly:15）が今日に当たるか。"""
    rule = task.repeat
    if not rule:
        return False
    kind, _, arg = rule.partition(":")
    kind, arg = kind.strip(), arg.strip()

    if kind == "daily":
        return True
    if kind == "weekdays":
        return today.weekday() < 5
    if kind == "weekends":
        return today.weekday() >= 5
    if kind == "weekly":
        wanted = [w.strip() for w in arg.replace("\u3001", ",").split(",") if w.strip()]
        for name in wanted:
            table = WEEKDAY_KEYS if name in WEEKDAY_KEYS else WEEKDAY_JA
            if name in table and table.index(name) == today.weekday():
                return True
        return False
    if kind == "monthly":
        try:
            return today.day == int(arg)
        except ValueError:
            return False
    return False


def _sorted(tasks: list[Task]) -> list[Task]:
    return sorted(
        tasks,
        key=lambda t: (
            PRIORITY_ORDER.get(t.priority, 1),
            t.due or date.max,
            t.title,
        ),
    )


def triage(tasks: list[Task], today: date, upcoming_days: int = 3) -> dict[str, list[Task]]:
    """今日やるもの / 期限切れ / もうすぐ、に振り分ける。"""
    overdue, today_tasks, upcoming = [], [], []
    for task in tasks:
        if task.done:
            continue
        if repeats_today(task, today):
            today_tasks.append(task)
            continue
        if task.due is None:
            continue
        delta = (task.due - today).days
        if delta < 0:
            overdue.append(task)
        elif delta == 0:
            today_tasks.append(task)
        elif delta <= upcoming_days:
            upcoming.append(task)
    return {
        "overdue": _sorted(overdue),
        "today": _sorted(today_tasks),
        "upcoming": _sorted(upcoming),
    }
