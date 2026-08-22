"""LINE Messaging API のプッシュ送信。

LINE Notify は 2025年3月で終了したため、Messaging API を使う。
LINE Developers でMessaging APIチャネルを作り、チャネルアクセストークン
（長期）を取得して LINE_CHANNEL_ACCESS_TOKEN に入れる。
"""

from __future__ import annotations

from typing import Any

import requests

PUSH_URL = "https://api.line.me/v2/bot/message/push"
BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
TIMEOUT = 20
MAX_LENGTH = 4900  # 1メッセージ5000字の制限に余裕をもたせる


def _chunks(text: str) -> list[str]:
    return [text[i:i + MAX_LENGTH] for i in range(0, len(text), MAX_LENGTH)] or [""]


def send(settings: dict[str, Any], subject: str, body: str) -> None:
    token = settings.get("channel_access_token")
    if not token:
        raise ValueError("line.channel_access_token が未設定です")
    to = settings.get("to")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # LINEは1リクエストにつきメッセージ5件まで
    messages = [{"type": "text", "text": chunk} for chunk in _chunks(f"{subject}\n\n{body}")][:5]
    payload: dict[str, Any] = {"messages": messages}
    url = BROADCAST_URL
    if to:
        payload["to"] = to
        url = PUSH_URL

    response = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    if response.status_code >= 400:
        raise RuntimeError(f"LINE API {response.status_code}: {response.text[:300]}")
