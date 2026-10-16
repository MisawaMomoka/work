"""Gmail の特定の件名のメールに、決まった文面で自動返信する。

仕組み:
  1. IMAP（アプリパスワード）で受信箱から「最近届いた・まだ返信していない」メールを探す
  2. 件名がルールに合うものに、SMTP で返信する（元のスレッドにぶら下がる）
  3. 返信したメールに Gmail のラベル（既定: auto-replied）を付け、二度返信しない

OAuth や Google Cloud の設定は不要。ブリーフィングのメール通知と同じ
アプリパスワードで動く。

返信の連鎖や迷惑メール化を防ぐため、次のメールには返信しない:
  - 自分自身から届いたもの
  - noreply / mailer-daemon などの送信専用アドレス
  - 自動返信・メーリングリスト・一斉配信（Auto-Submitted / Precedence / List-Id）
"""

from __future__ import annotations

import imaplib
import logging
import os
import re
import smtplib
import time
from dataclasses import dataclass
from datetime import datetime
from email import message_from_string, policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import format_datetime, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

TIMEOUT = 30
DEFAULT_LABEL = "auto-replied"

_NO_REPLY_PATTERN = re.compile(
    r"(^|[._+-])(no-?reply|do-?not-?reply|donotreply|mailer-daemon|postmaster|bounces?)([._+-]|@)",
    re.IGNORECASE,
)


@dataclass
class Rule:
    name: str
    subject_contains: list[str]
    subject_regex: re.Pattern[str] | None
    body: str
    subject: str | None = None  # 返信の件名（未指定なら "Re: 元の件名"）

    def matches(self, subject: str) -> bool:
        if self.subject_regex is not None and self.subject_regex.search(subject):
            return True
        folded = _fold(subject)
        return any(_fold(word) in folded for word in self.subject_contains)


@dataclass
class Candidate:
    uid: bytes
    message: Message
    subject: str
    rule: Rule | None
    skip: str | None  # 返信しない理由（None なら返信する）
    to_addr: str
    to_name: str


# ------------------------------------------------------------------ 設定
def parse_rules(settings: dict[str, Any]) -> list[Rule]:
    rules: list[Rule] = []
    for index, raw in enumerate(settings.get("rules") or [], start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"autoreply.rules の {index} 件目が辞書ではありません")
        name = str(raw.get("name") or f"ルール{index}")
        contains = raw.get("subject_contains") or []
        if isinstance(contains, str):
            contains = [contains]
        contains = [str(word) for word in contains if str(word).strip()]
        pattern = raw.get("subject_regex")
        regex = re.compile(pattern, re.IGNORECASE) if pattern else None
        body = raw.get("body")
        if not (contains or regex):
            raise ValueError(f"autoreply のルール「{name}」に subject_contains か subject_regex が必要です")
        if not body or not str(body).strip():
            raise ValueError(f"autoreply のルール「{name}」に body（返信文）が必要です")
        rules.append(Rule(name, contains, regex, str(body), raw.get("subject") or None))
    return rules


def _fold(text: str) -> str:
    """全角/半角・大文字/小文字・空白の違いを無視して比べるための正規化。"""
    import unicodedata

    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


# ------------------------------------------------------------------ 判定
def decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


def reply_target(message: Message) -> tuple[str, str]:
    """返信先（Reply-To があればそちら）の (名前, アドレス)。"""
    for header in ("Reply-To", "From"):
        pairs = getaddresses([decode(message.get(header))])
        for name, addr in pairs:
            if addr and "@" in addr:
                return name.strip().strip('"'), addr.strip()
    return "", ""


def skip_reason(message: Message, to_addr: str, own_addrs: set[str]) -> str | None:
    if not to_addr:
        return "返信先アドレスがない"
    _, from_addr = parseaddr(decode(message.get("From")))
    if to_addr.lower() in own_addrs or from_addr.lower() in own_addrs:
        return "自分から届いたメール"
    if _NO_REPLY_PATTERN.search(to_addr):
        return "送信専用アドレス"
    auto = (message.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return "自動送信されたメール"
    precedence = (message.get("Precedence") or "").strip().lower()
    if precedence in {"bulk", "list", "junk", "auto_reply"}:
        return "一斉配信のメール"
    if message.get("List-Id") or message.get("X-Autoreply") or message.get("X-Autorespond"):
        return "メーリングリスト / 自動返信"
    return None


def evaluate(uid: bytes, raw_header: bytes, rules: list[Rule], own_addrs: set[str]) -> Candidate:
    # ヘッダーは本来ASCIIだが、生のUTF-8で届くこともあるので文字列にしてから読む
    message = message_from_string(raw_header.decode("utf-8", "replace"), policy=policy.compat32)
    subject = decode(message.get("Subject"))
    rule = next((r for r in rules if r.matches(subject)), None)
    name, addr = reply_target(message)
    skip = None if rule is None else skip_reason(message, addr, own_addrs)
    return Candidate(uid, message, subject, rule, skip, addr, name)


# ------------------------------------------------------------------ 返信文
def render(template: str, candidate: Candidate, tz: ZoneInfo) -> str:
    """{name} {email} {subject} {date} を差し込む。それ以外の { } はそのまま残す。"""
    received = ""
    try:
        received_at = parsedate_to_datetime(candidate.message.get("Date"))
        if received_at:
            local = received_at.astimezone(tz)
            received = f"{local.year}年{local.month}月{local.day}日 {local:%H:%M}"
    except (TypeError, ValueError):
        pass
    values = {
        "{name}": candidate.to_name or candidate.to_addr,
        "{email}": candidate.to_addr,
        "{subject}": candidate.subject,
        "{date}": received,
    }
    text = template
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def build_reply(candidate: Candidate, from_addr: str, from_name: str, tz: ZoneInfo) -> EmailMessage:
    assert candidate.rule is not None
    original = candidate.message
    subject = candidate.rule.subject
    if subject:
        subject = render(subject, candidate, tz)
    else:
        subject = candidate.subject if re.match(r"^\s*re\s*:", candidate.subject, re.I) else f"Re: {candidate.subject}"

    reply = EmailMessage()
    reply["Subject"] = subject
    reply["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    reply["To"] = f"{candidate.to_name} <{candidate.to_addr}>" if candidate.to_name else candidate.to_addr
    reply["Date"] = format_datetime(datetime.now(tz))
    reply["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1] or None)
    message_id = (original.get("Message-ID") or "").strip()
    if message_id:
        # 元のメールと同じスレッドに表示させる
        reply["In-Reply-To"] = message_id
        references = (original.get("References") or "").split()
        reply["References"] = " ".join([*references, message_id])
    # 相手側の自動返信と打ち返し合わないための目印（RFC 3834）
    reply["Auto-Submitted"] = "auto-replied"
    reply.set_content(render(candidate.rule.body, candidate, tz))
    return reply


def mask_addr(addr: str) -> str:
    """公開リポジトリの Actions ログに相手のアドレスを残さないための伏せ字。"""
    local, _, domain = addr.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


# ------------------------------------------------------------------ Gmail とのやりとり
class Mailbox:
    def __init__(self, settings: dict[str, Any]):
        self.settings = settings
        self.username = settings.get("username") or ""
        self.password = settings.get("password") or ""
        self.label = settings.get("done_label") or DEFAULT_LABEL
        if not (self.username and self.password):
            raise ValueError("autoreply の username / password（Gmailのアプリパスワード）が未設定です")
        if not re.fullmatch(r"[A-Za-z0-9_\-/]+", self.label):
            raise ValueError("autoreply.done_label は半角英数字と - _ / だけにしてください")
        self.imap = imaplib.IMAP4_SSL(settings.get("imap_host") or "imap.gmail.com", timeout=TIMEOUT)

    def __enter__(self) -> "Mailbox":
        self.imap.login(self.username, self.password)
        self.imap.select("INBOX")
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.imap.logout()
        except Exception:
            pass

    def search(self, lookback_days: int) -> list[bytes]:
        # Gmail の検索構文がそのまま使える拡張（X-GM-RAW）
        query = f'"newer_than:{int(lookback_days)}d -label:{self.label} -from:me"'
        typ, data = self.imap.uid("SEARCH", "X-GM-RAW", query)
        if typ != "OK":
            raise RuntimeError(f"IMAP の検索に失敗しました: {data!r}")
        return data[0].split() if data and data[0] else []

    def fetch_header(self, uid: bytes) -> bytes:
        # PEEK なので既読にはならない
        typ, data = self.imap.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
        if typ != "OK":
            raise RuntimeError(f"IMAP の取得に失敗しました: {data!r}")
        for part in data:
            if isinstance(part, tuple):
                return part[1]
        return b""

    def set_label(self, uid: bytes, on: bool) -> None:
        typ, data = self.imap.uid("STORE", uid, "+X-GM-LABELS" if on else "-X-GM-LABELS", f'("{self.label}")')
        if typ != "OK":
            raise RuntimeError(f"ラベルの付け外しに失敗しました: {data!r}")

    def drafts_folder(self) -> str:
        # 下書きフォルダ名は Gmail の表示言語で変わる（[Gmail]/下書き など）ので特殊用途フラグで探す
        typ, data = self.imap.list()
        for line in data or []:
            text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
            if "\\Drafts" in text:
                match = re.search(r'"([^"]+)"\s*$', text) or re.search(r"(\S+)\s*$", text)
                if match:
                    return match.group(1)
        raise RuntimeError("下書きフォルダが見つかりません")

    def save_draft(self, reply: EmailMessage) -> None:
        folder = self.drafts_folder()
        typ, data = self.imap.append(
            f'"{folder}"', "(\\Draft)", imaplib.Time2Internaldate(time.time()), reply.as_bytes()
        )
        if typ != "OK":
            raise RuntimeError(f"下書きの保存に失敗しました: {data!r}")


def send_smtp(settings: dict[str, Any], reply: EmailMessage) -> None:
    host = settings.get("smtp_host") or "smtp.gmail.com"
    port = int(settings.get("smtp_port") or 587)
    username, password = settings.get("username"), settings.get("password")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT) as server:
            server.login(username, password)
            server.send_message(reply)
        return
    with smtplib.SMTP(host, port, timeout=TIMEOUT) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(reply)


# ------------------------------------------------------------------ 本体
@dataclass
class RunResult:
    replied: int = 0
    skipped: int = 0
    failed: int = 0
    deferred: int = 0


def run(settings: dict[str, Any], tz: ZoneInfo, dry_run: bool = False, out=print) -> RunResult:
    rules = parse_rules(settings)
    if not rules:
        raise ValueError("autoreply.rules が空です。返信したい件名と文面を設定してください")

    mode = (settings.get("mode") or "send").lower()
    if mode not in {"send", "draft"}:
        raise ValueError("autoreply.mode は send か draft にしてください")
    lookback = int(settings.get("lookback_days") or 1)
    limit = int(settings.get("max_per_run") or 10)
    from_addr = settings.get("from_addr") or settings.get("username") or ""
    from_name = settings.get("from_name") or ""
    own = {a.lower() for a in [settings.get("username"), from_addr, *(settings.get("own_addresses") or [])] if a}
    # 公開リポジトリの Actions ログには相手のアドレスや件名を出さない
    public_log = os.environ.get("GITHUB_ACTIONS") == "true"

    def show(c: Candidate) -> str:
        if public_log:
            return f"[{c.rule.name}] {mask_addr(c.to_addr)}"
        return f"[{c.rule.name}] {c.to_name} <{c.to_addr}> 「{c.subject}」"

    result = RunResult()
    replied_to: set[str] = set()
    with Mailbox(settings) as box:
        uids = box.search(lookback)
        log.debug("対象期間のメール: %d件", len(uids))
        for uid in uids:
            candidate = evaluate(uid, box.fetch_header(uid), rules, own)
            if candidate.rule is None:
                continue
            if candidate.skip is None and candidate.to_addr.lower() in replied_to:
                candidate.skip = "同じ相手に今回すでに返信した"
            if candidate.skip:
                result.skipped += 1
                out(f"  - 返信しない: {show(candidate)}（{candidate.skip}）")
                continue
            if result.replied >= limit:
                result.deferred += 1
                continue

            reply = build_reply(candidate, from_addr, from_name, tz)
            if dry_run:
                result.replied += 1
                replied_to.add(candidate.to_addr.lower())
                out(f"  ✓ 返信予定: {show(candidate)}")
                if not public_log:
                    out(f"    件名: {reply['Subject']}")
                    out("    " + reply.get_content().rstrip().replace("\n", "\n    "))
                continue

            # 先にラベルを付けてから送る。ラベルが付かないまま送ると、
            # 次の実行でまた同じメールに返信してしまうため
            box.set_label(uid, True)
            try:
                if mode == "draft":
                    box.save_draft(reply)
                else:
                    send_smtp(settings, reply)
            except Exception as exc:
                result.failed += 1
                out(f"  ✗ 失敗: {show(candidate)}: {type(exc).__name__}: {exc}")
                try:
                    box.set_label(uid, False)  # 次回やり直せるように戻す
                except Exception:
                    log.warning("ラベルを外せませんでした。このメールは手動で返信してください")
                continue
            result.replied += 1
            replied_to.add(candidate.to_addr.lower())
            out(f"  ✓ {'下書き保存' if mode == 'draft' else '返信'}: {show(candidate)}")

    return result
