"""通知チャネル。設定で enabled にしたものすべてに同じ本文を送る。"""

from __future__ import annotations

import logging
from typing import Any, Callable

from . import console, discord, email, line, slack

logger = logging.getLogger(__name__)

SENDERS: dict[str, Callable[[dict[str, Any], str, str], None]] = {
    "console": console.send,
    "line": line.send,
    "slack": slack.send,
    "discord": discord.send,
    "email": email.send,
}


def dispatch(notify_config: dict[str, Any], subject: str, body: str) -> list[str]:
    """送信を試み、成功したチャネル名を返す。1つ失敗しても他は続行する。"""
    delivered: list[str] = []
    for name, settings in (notify_config or {}).items():
        if not isinstance(settings, dict) or not settings.get("enabled"):
            continue
        sender = SENDERS.get(name)
        if sender is None:
            logger.warning("未知の通知チャネル: %s", name)
            continue
        try:
            sender(settings, subject, body)
            delivered.append(name)
        except Exception as exc:
            logger.error("%s への送信に失敗: %s: %s", name, type(exc).__name__, exc)
    return delivered
