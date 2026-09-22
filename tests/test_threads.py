import json
from datetime import date
from zoneinfo import ZoneInfo

import pytest
import requests

from secretary.sources import threads

TZ = ZoneInfo("Asia/Tokyo")
SETTINGS = threads.settings_from({"access_token": "TOKEN", "username": "zuck"})


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def fake_get(responses):
    """requests.get を、渡した応答を順に返すだけのものに差し替える。"""
    calls = []

    def _get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        return responses[len(calls) - 1]

    _get.calls = calls
    return _get


# ---------------------------------------------------------------- 入力の正規化
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("zuck", "zuck"),
        ("@zuck", "zuck"),
        ("  @zuck  ", "zuck"),
        ("https://www.threads.net/@zuck", "zuck"),
        ("https://www.threads.com/@zuck/post/ABC123", "zuck"),
        ("", ""),
    ],
)
def test_normalize_username(raw, expected):
    assert threads.normalize_username(raw) == expected


def test_settings_from_fills_defaults():
    settings = threads.settings_from(None)
    assert settings["api_base"] == threads.DEFAULT_API_BASE
    assert settings["out_file"] == "data/threads_posts.csv"
    assert settings["include_replies"] is True


def test_missing_token_is_a_friendly_error(monkeypatch):
    with pytest.raises(threads.ThreadsError) as exc:
        threads.lookup_profile(threads.settings_from({}), "zuck")
    assert "THREADS_ACCESS_TOKEN" in str(exc.value)


def test_empty_username_is_rejected():
    with pytest.raises(threads.ThreadsError):
        threads.lookup_profile(SETTINGS, "  ")


# ---------------------------------------------------------------- プロフィール
def test_lookup_profile(monkeypatch):
    get = fake_get([FakeResponse({"id": "123", "username": "zuck", "name": "Mark"})])
    monkeypatch.setattr(requests, "get", get)

    profile = threads.lookup_profile(SETTINGS, "@zuck")

    assert profile["id"] == "123"
    url, params = get.calls[0]
    assert url.endswith("/profile_lookup")
    assert params["username"] == "zuck"
    assert params["access_token"] == "TOKEN"


def test_permission_error_explains_app_review(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        fake_get([FakeResponse({"error": {"message": "permissions error", "code": 10}}, 403)]),
    )
    with pytest.raises(threads.ThreadsError) as exc:
        threads.lookup_profile(SETTINGS, "zuck")
    assert "threads_profile_discovery" in str(exc.value)


def test_rate_limit_error_tells_you_to_wait(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        fake_get([FakeResponse({"error": {"message": "limit", "code": 4}}, 400)]),
    )
    with pytest.raises(threads.ThreadsError) as exc:
        threads.lookup_profile(SETTINGS, "zuck")
    assert "上限" in str(exc.value)


# ---------------------------------------------------------------- 投稿の取得
def post(post_id, timestamp, text="本文", **extra):
    return {
        "id": post_id,
        "username": "zuck",
        "text": text,
        "timestamp": timestamp,
        "permalink": f"https://www.threads.net/@zuck/post/{post_id}",
        "media_type": "TEXT_POST",
        **extra,
    }


def test_iter_posts_follows_pagination(monkeypatch):
    page1 = FakeResponse(
        {
            "data": [post("1", "2026-01-01T00:00:00+0000")],
            "paging": {"next": "https://graph.threads.net/next-page"},
        }
    )
    page2 = FakeResponse({"data": [post("2", "2026-01-02T00:00:00+0000")]})
    get = fake_get([page1, page2])
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(threads.time, "sleep", lambda _: None)

    posts = list(threads.iter_posts(SETTINGS, "123", tz=TZ))

    assert [p["id"] for p in posts] == ["1", "2"]
    assert get.calls[1][0] == "https://graph.threads.net/next-page"


def test_iter_posts_stops_at_max_pages(monkeypatch):
    endless = FakeResponse(
        {"data": [post("1", "2026-01-01T00:00:00+0000")],
         "paging": {"next": "https://graph.threads.net/next-page"}}
    )
    monkeypatch.setattr(requests, "get", fake_get([endless] * 10))
    monkeypatch.setattr(threads.time, "sleep", lambda _: None)

    posts = list(threads.iter_posts(SETTINGS, "123", tz=TZ, max_pages=3))
    assert len(posts) == 3


def test_since_and_until_become_unix_timestamps(monkeypatch):
    get = fake_get([FakeResponse({"data": []})])
    monkeypatch.setattr(requests, "get", get)

    list(threads.iter_posts(SETTINGS, "123", tz=TZ,
                            since=date(2026, 1, 1), until=date(2026, 1, 31)))

    _, params = get.calls[0]
    assert params["since"] == 1767193200      # 2026-01-01 00:00 JST
    assert params["until"] == 1769871599      # 2026-01-31 23:59:59 JST


def test_unknown_fields_are_dropped_and_retried(monkeypatch):
    refusal = FakeResponse(
        {"error": {"message": "(#100) Tried accessing nonexisting field (topic_tag) on node type (Thread)",
                   "code": 100}},
        400,
    )
    ok = FakeResponse({"data": [post("1", "2026-01-01T00:00:00+0000")]})
    get = fake_get([refusal, ok])
    monkeypatch.setattr(requests, "get", get)

    posts = list(threads.iter_posts(SETTINGS, "123", tz=TZ))

    assert len(posts) == 1
    assert "topic_tag" not in get.calls[1][1]["fields"]


# ---------------------------------------------------------------- 整形
def test_timestamp_is_converted_to_local_time():
    row = threads.to_row(post("1", "2026-01-01T00:00:00+0000"), TZ)
    assert row["posted_at"] == "2026-01-01 09:00"   # UTC+9
    assert row["timestamp"] == "2026-01-01T00:00:00+0000"


def test_broken_timestamp_does_not_crash():
    row = threads.to_row(post("1", "いつか"), TZ)
    assert row["posted_at"] == ""


@pytest.mark.parametrize(
    "extra, expected",
    [
        ({}, "投稿"),
        ({"is_reply": True}, "返信"),
        ({"replied_to": {"id": "9"}}, "返信"),
        ({"is_quote_post": True}, "引用"),
        ({"reposted_post": {"id": "9"}}, "リポスト"),
    ],
)
def test_classify(extra, expected):
    assert threads.classify(post("1", "2026-01-01T00:00:00+0000", **extra)) == expected


def test_reference_ids_are_flattened():
    row = threads.to_row(
        post("1", "2026-01-01T00:00:00+0000", replied_to={"id": "9"}, root_post={"id": "7"}), TZ
    )
    assert row["replied_to_id"] == "9"
    assert row["root_post_id"] == "7"


# ---------------------------------------------------------------- CSV
def test_merge_keeps_newest_first_and_dedupes():
    old = [threads.to_row(post("1", "2026-01-01T00:00:00+0000", text="古い"), TZ)]
    new = [
        threads.to_row(post("1", "2026-01-01T00:00:00+0000", text="編集後"), TZ),
        threads.to_row(post("2", "2026-02-01T00:00:00+0000"), TZ),
    ]
    merged = threads.merge_rows(old, new)

    assert [r["id"] for r in merged] == ["2", "1"]
    assert merged[1]["text"] == "編集後"


def test_csv_round_trip(tmp_path):
    path = tmp_path / "out.csv"
    rows = [threads.to_row(post("1", "2026-01-01T00:00:00+0000", text="改行\nあり,カンマも"), TZ)]

    assert threads.write_csv(path, rows) == 1
    loaded = threads.load_rows(path)

    assert len(loaded) == 1
    assert loaded[0]["text"] == "改行\nあり,カンマも"
    # Excelで文字化けしないように BOM 付きで書く
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_load_rows_missing_file_is_empty(tmp_path):
    assert threads.load_rows(tmp_path / "nope.csv") == []


# ---------------------------------------------------------------- まとめ役
def test_export_merges_with_existing_csv(monkeypatch, tmp_path):
    path = tmp_path / "out.csv"
    threads.write_csv(path, [threads.to_row(post("1", "2026-01-01T00:00:00+0000"), TZ)])

    monkeypatch.setattr(
        requests,
        "get",
        fake_get(
            [
                FakeResponse({"id": "123", "username": "zuck", "name": "Mark"}),
                FakeResponse({"data": [post("2", "2026-02-01T00:00:00+0000")]}),
            ]
        ),
    )

    result = threads.export(SETTINGS, "@zuck", tz=TZ, out_file=path)

    assert result.fetched == 1
    assert result.added == 1
    assert result.total == 2
    assert [r["id"] for r in result.rows] == ["2", "1"]


def test_export_can_drop_replies(monkeypatch, tmp_path):
    monkeypatch.setattr(
        requests,
        "get",
        fake_get(
            [
                FakeResponse({"id": "123", "username": "zuck"}),
                FakeResponse(
                    {
                        "data": [
                            post("1", "2026-01-01T00:00:00+0000"),
                            post("2", "2026-01-02T00:00:00+0000", is_reply=True),
                        ]
                    }
                ),
            ]
        ),
    )

    result = threads.export(
        SETTINGS, "zuck", tz=TZ, include_replies=False, out_file=tmp_path / "out.csv"
    )
    assert [r["id"] for r in result.rows] == ["1"]
    assert result.fetched == 2      # 取ったのは2件、残したのは1件
    assert result.added == 1


def test_export_fresh_ignores_existing_csv(monkeypatch, tmp_path):
    path = tmp_path / "out.csv"
    threads.write_csv(path, [threads.to_row(post("1", "2026-01-01T00:00:00+0000"), TZ)])

    monkeypatch.setattr(
        requests,
        "get",
        fake_get(
            [
                FakeResponse({"id": "123", "username": "zuck"}),
                FakeResponse({"data": [post("2", "2026-02-01T00:00:00+0000")]}),
            ]
        ),
    )

    result = threads.export(SETTINGS, "zuck", tz=TZ, fresh=True, out_file=path)
    assert [r["id"] for r in result.rows] == ["2"]
