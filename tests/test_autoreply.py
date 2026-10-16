from email.header import Header
from zoneinfo import ZoneInfo

import pytest

from secretary import autoreply

TZ = ZoneInfo("Asia/Tokyo")

RULES = {
    "rules": [
        {
            "name": "問い合わせ",
            "subject_contains": ["お問い合わせ"],
            "body": "{name} 様\n「{subject}」を {date} に受け付けました。{email}\n{unknown}",
        }
    ]
}


def header(subject="お問い合わせ: 料金について", from_="山田 太郎 <taro@example.com>", **extra) -> bytes:
    lines = [
        f"From: {from_}",
        "To: me@gmail.com",
        f"Subject: {Header(subject, 'utf-8').encode()}",
        "Date: Thu, 15 Oct 2026 01:30:00 +0000",
        "Message-ID: <orig-1@example.com>",
    ]
    lines += [f"{k.replace('_', '-')}: {v}" for k, v in extra.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def evaluate(raw: bytes):
    return autoreply.evaluate(b"1", raw, autoreply.parse_rules(RULES), {"me@gmail.com"})


# ------------------------------------------------------------------ ルール
def test_rule_matches_ignoring_width_and_case():
    rule = autoreply.parse_rules({"rules": [{"subject_contains": ["ｾﾐﾅｰ申込", "ABC"], "body": "x"}]})[0]
    assert rule.matches("【セミナー 申込】10月回")
    assert rule.matches("re: abc の件")
    assert not rule.matches("セミナーのご案内")


def test_rule_regex():
    rule = autoreply.parse_rules({"rules": [{"subject_regex": "^【申込】", "body": "x"}]})[0]
    assert rule.matches("【申込】山田")
    assert not rule.matches("Re: 【申込】山田")


@pytest.mark.parametrize("raw", [{"body": "x"}, {"subject_contains": ["a"]}, {"subject_contains": ["a"], "body": " "}])
def test_rule_requires_subject_and_body(raw):
    with pytest.raises(ValueError):
        autoreply.parse_rules({"rules": [raw]})


def test_unmatched_subject_has_no_rule():
    assert evaluate(header(subject="来週の打ち合わせ")).rule is None


# ------------------------------------------------------------------ 返信しないメール
def test_normal_mail_is_replied():
    c = evaluate(header())
    assert c.rule is not None and c.skip is None
    assert (c.to_name, c.to_addr) == ("山田 太郎", "taro@example.com")


def test_reply_to_is_preferred():
    # フォームの通知メールは From が送信専用、Reply-To がお客さん、というのがよくある形
    c = evaluate(header(from_="Form <noreply@forms.example>", Reply_To="hanako@example.com"))
    assert c.skip is None
    assert c.to_addr == "hanako@example.com"


@pytest.mark.parametrize(
    "extra",
    [
        {"from_": "me@gmail.com"},
        {"from_": "no-reply@shop.example"},
        {"from_": "MAILER-DAEMON@mx.example"},
        {"Auto_Submitted": "auto-replied"},
        {"Precedence": "bulk"},
        {"List_Id": "<news.example.com>"},
    ],
)
def test_loops_and_bulk_are_skipped(extra):
    assert evaluate(header(**extra)).skip


# ------------------------------------------------------------------ 返信文
def test_build_reply_threads_and_fills_placeholders():
    c = evaluate(header(References="<a@x>"))
    reply = autoreply.build_reply(c, "me@gmail.com", "三沢", TZ)
    assert reply["Subject"] == "Re: お問い合わせ: 料金について"
    assert reply["In-Reply-To"] == "<orig-1@example.com>"
    assert reply["References"] == "<a@x> <orig-1@example.com>"
    assert reply["Auto-Submitted"] == "auto-replied"
    assert "taro@example.com" in reply["To"]
    body = reply.get_content()
    assert "山田 太郎 様" in body
    assert "2026年10月15日 10:30" in body  # 日本時間に直す
    assert "{unknown}" in body  # 知らない差し込みは壊さずに残す


def test_existing_re_is_not_doubled_and_custom_subject():
    c = evaluate(header(subject="Re: お問い合わせ"))
    assert autoreply.build_reply(c, "me@gmail.com", "", TZ)["Subject"] == "Re: お問い合わせ"
    c.rule.subject = "受付: {subject}"
    assert autoreply.build_reply(c, "me@gmail.com", "", TZ)["Subject"] == "受付: Re: お問い合わせ"


def test_mask_addr():
    assert autoreply.mask_addr("taro@example.com") == "t***@example.com"


# ------------------------------------------------------------------ 実行の流れ
class FakeBox:
    def __init__(self, mails, events):
        self.mails = mails
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def search(self, lookback_days):
        return list(self.mails)

    def fetch_header(self, uid):
        return self.mails[uid]

    def set_label(self, uid, on):
        self.events.append(("label" if on else "unlabel", uid))

    def save_draft(self, reply):
        self.events.append(("draft", reply["To"]))


@pytest.fixture
def mailbox(monkeypatch):
    events: list = []
    mails: dict = {}
    monkeypatch.setattr(autoreply, "Mailbox", lambda settings: FakeBox(mails, events))
    monkeypatch.setattr(autoreply, "send_smtp", lambda settings, reply: events.append(("send", reply["To"])))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return mails, events


SETTINGS = {**RULES, "username": "me@gmail.com", "password": "x"}


def test_run_labels_before_sending(mailbox):
    mails, events = mailbox
    mails[b"1"] = header()
    mails[b"2"] = header(subject="関係ないメール")
    result = autoreply.run(SETTINGS, TZ, out=lambda *_: None)
    assert result.replied == 1
    assert [e[0] for e in events] == ["label", "send"]


def test_dry_run_touches_nothing(mailbox):
    mails, events = mailbox
    mails[b"1"] = header()
    lines = []
    result = autoreply.run(SETTINGS, TZ, dry_run=True, out=lines.append)
    assert result.replied == 1 and events == []
    assert any("山田 太郎 様" in line for line in lines)


def test_failed_send_removes_label_for_retry(mailbox, monkeypatch):
    mails, events = mailbox
    mails[b"1"] = header()

    def boom(settings, reply):
        raise OSError("smtp down")

    monkeypatch.setattr(autoreply, "send_smtp", boom)
    result = autoreply.run(SETTINGS, TZ, out=lambda *_: None)
    assert result.failed == 1 and result.replied == 0
    assert events == [("label", b"1"), ("unlabel", b"1")]


def test_limit_and_same_sender_once(mailbox):
    mails, events = mailbox
    mails[b"1"] = header()
    mails[b"2"] = header()  # 同じ相手から2通 → 1通だけ返信
    mails[b"3"] = header(from_="a@example.com")
    mails[b"4"] = header(from_="b@example.com")
    result = autoreply.run({**SETTINGS, "max_per_run": 2}, TZ, out=lambda *_: None)
    assert (result.replied, result.skipped, result.deferred) == (2, 1, 1)
    assert len([e for e in events if e[0] == "send"]) == 2


def test_draft_mode(mailbox):
    mails, events = mailbox
    mails[b"1"] = header()
    autoreply.run({**SETTINGS, "mode": "draft"}, TZ, out=lambda *_: None)
    assert [e[0] for e in events] == ["label", "draft"]


def test_public_log_hides_address_and_subject(mailbox, monkeypatch):
    mails, _ = mailbox
    mails[b"1"] = header()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    lines = []
    autoreply.run(SETTINGS, TZ, dry_run=True, out=lines.append)
    joined = "\n".join(lines)
    assert "taro@example.com" not in joined and "料金" not in joined and "山田" not in joined
    assert "t***@example.com" in joined


def test_cli_does_nothing_when_disabled(capsys):
    from secretary.__main__ import main

    assert main(["autoreply"]) == 0
    assert "enabled が false" in capsys.readouterr().out
