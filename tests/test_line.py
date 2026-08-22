"""LINE通知の単体テスト。実際のAPIは叩かず requests を差し替える。"""

import json

import pytest

from secretary.notify import line as line_notify

TOKEN_SETTINGS = {"enabled": True, "channel_access_token": "dummy-token"}


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload, ensure_ascii=False)
        self.content = self.text.encode()

    def json(self):
        return self._payload


class Recorder:
    """呼ばれたリクエストを記録する requests.post / get の代役。"""

    def __init__(self, response=None):
        self.calls = []
        self.response = response or FakeResponse()

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.response


def test_missing_token_is_a_clear_error():
    with pytest.raises(line_notify.LineError, match="LINE_CHANNEL_ACCESS_TOKEN"):
        line_notify.send({"enabled": True}, "件名", "本文")


def test_send_without_user_id_uses_broadcast(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(line_notify.requests, "post", recorder)

    line_notify.send(TOKEN_SETTINGS, "件名", "本文")

    call = recorder.calls[0]
    assert call["url"] == line_notify.BROADCAST_URL
    assert "to" not in call["json"]
    assert call["headers"]["Authorization"] == "Bearer dummy-token"
    assert call["json"]["messages"][0]["text"] == "件名\n\n本文"


def test_send_with_user_id_uses_push(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(line_notify.requests, "post", recorder)

    line_notify.send({**TOKEN_SETTINGS, "to": "U123"}, "件名", "本文")

    call = recorder.calls[0]
    assert call["url"] == line_notify.PUSH_URL
    assert call["json"]["to"] == "U123"


def test_blank_user_id_is_treated_as_broadcast(monkeypatch):
    """未設定の環境変数は空文字になるので、それを送信先と誤解しないこと。"""
    recorder = Recorder()
    monkeypatch.setattr(line_notify.requests, "post", recorder)

    line_notify.send({**TOKEN_SETTINGS, "to": "   "}, "件名", "本文")

    assert recorder.calls[0]["url"] == line_notify.BROADCAST_URL


def test_long_body_is_split_and_capped(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(line_notify.requests, "post", recorder)

    line_notify.send(TOKEN_SETTINGS, "件名", "あ" * 40000)

    messages = recorder.calls[0]["json"]["messages"]
    assert len(messages) == line_notify.MAX_MESSAGES
    assert all(len(m["text"]) <= line_notify.MAX_LENGTH for m in messages)


def test_401_error_includes_a_fix_hint(monkeypatch):
    monkeypatch.setattr(
        line_notify.requests, "post",
        Recorder(FakeResponse(401, {"message": "Authentication failed"})),
    )
    with pytest.raises(line_notify.LineError) as exc:
        line_notify.send(TOKEN_SETTINGS, "件名", "本文")
    assert "401" in str(exc.value)
    assert "再発行" in str(exc.value)


def test_429_error_mentions_the_quota(monkeypatch):
    monkeypatch.setattr(
        line_notify.requests, "post", Recorder(FakeResponse(429, {"message": "quota"}))
    )
    with pytest.raises(line_notify.LineError, match="使い切"):
        line_notify.send(TOKEN_SETTINGS, "件名", "本文")


def test_bot_info(monkeypatch):
    recorder = Recorder(FakeResponse(200, {"displayName": "秘書", "basicId": "@abc"}))
    monkeypatch.setattr(line_notify.requests, "get", recorder)

    assert line_notify.bot_info(TOKEN_SETTINGS)["displayName"] == "秘書"
    assert recorder.calls[0]["url"] == line_notify.INFO_URL


def test_quota_combines_limit_and_usage(monkeypatch):
    responses = {
        line_notify.QUOTA_URL: FakeResponse(200, {"type": "limited", "value": 200}),
        line_notify.CONSUMPTION_URL: FakeResponse(200, {"totalUsage": 12}),
    }

    def fake_get(url, headers=None, timeout=None):
        return responses[url]

    monkeypatch.setattr(line_notify.requests, "get", fake_get)

    assert line_notify.quota(TOKEN_SETTINGS) == {"type": "limited", "limit": 200, "used": 12}


def test_dispatch_reports_line_failure_without_blocking_others(monkeypatch, capsys):
    """LINEが落ちていても、他チャネルへの通知は続くこと。"""
    from secretary import notify

    monkeypatch.setattr(
        line_notify.requests, "post", Recorder(FakeResponse(401, {"message": "bad token"}))
    )
    delivered = notify.dispatch(
        {"line": TOKEN_SETTINGS, "console": {"enabled": True}}, "件名", "本文"
    )
    assert delivered == ["console"]
    assert "本文" in capsys.readouterr().out
