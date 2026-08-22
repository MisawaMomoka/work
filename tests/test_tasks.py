from datetime import date

from secretary.models import Task
from secretary.sources.tasks import load_tasks, repeats_today, save_tasks, triage


def test_load_accepts_strings_and_dicts(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "- 牛乳を買う\n"
        "- title: 請求書を送る\n"
        "  due: 2026-08-25\n"
        "  priority: high\n",
        encoding="utf-8",
    )
    tasks = load_tasks(path)
    assert [t.title for t in tasks] == ["牛乳を買う", "請求書を送る"]
    assert tasks[1].due == date(2026, 8, 25)
    assert tasks[1].priority == "high"


def test_save_roundtrip(tmp_path):
    path = tmp_path / "tasks.yaml"
    save_tasks(path, [Task(title="日報", repeat="weekdays", priority="low", done=True)])
    restored = load_tasks(path)[0]
    assert (restored.title, restored.repeat, restored.priority, restored.done) == (
        "日報",
        "weekdays",
        "low",
        True,
    )


def test_repeat_rules():
    monday, saturday = date(2026, 8, 17), date(2026, 8, 22)
    assert repeats_today(Task(title="x", repeat="daily"), saturday)
    assert repeats_today(Task(title="x", repeat="weekdays"), monday)
    assert not repeats_today(Task(title="x", repeat="weekdays"), saturday)
    assert repeats_today(Task(title="x", repeat="weekends"), saturday)
    assert repeats_today(Task(title="x", repeat="weekly:mon"), monday)
    assert repeats_today(Task(title="x", repeat="weekly:月"), monday)
    assert not repeats_today(Task(title="x", repeat="weekly:tue"), monday)
    assert repeats_today(Task(title="x", repeat="monthly:17"), monday)
    assert not repeats_today(Task(title="x", repeat="monthly:18"), monday)
    assert not repeats_today(Task(title="x", repeat=""), monday)
    assert not repeats_today(Task(title="x", repeat="なにこれ:99"), monday)


def test_triage_buckets():
    today = date(2026, 8, 20)
    tasks = [
        Task(title="遅れている", due=date(2026, 8, 18)),
        Task(title="今日", due=today),
        Task(title="明後日", due=date(2026, 8, 22)),
        Task(title="ずっと先", due=date(2026, 9, 30)),
        Task(title="毎日やる", repeat="daily"),
        Task(title="完了済み", due=today, done=True),
        Task(title="期限なし"),
    ]
    buckets = triage(tasks, today, upcoming_days=3)
    assert [t.title for t in buckets["overdue"]] == ["遅れている"]
    assert sorted(t.title for t in buckets["today"]) == ["今日", "毎日やる"]
    assert [t.title for t in buckets["upcoming"]] == ["明後日"]


def test_triage_sorts_high_priority_first():
    today = date(2026, 8, 20)
    buckets = triage(
        [
            Task(title="ふつう", due=today),
            Task(title="至急", due=today, priority="high"),
        ],
        today,
    )
    assert [t.title for t in buckets["today"]] == ["至急", "ふつう"]
