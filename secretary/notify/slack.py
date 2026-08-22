"""Slack Incoming Webhook。"""

from __future__ import annotations

from typing import Any

import requests

TIMEOUT = 20


def send(settings: dict[str, Any], subject: str, body: str) -> None:
    url = settings.get("webhook_url")
    if not url:
        raise ValueError("slack.webhook_url が未設定です")
    response = requests.post(
        url, json={"text": f"*{subject}*\n{body}"}, timeout=TIMEOUT
    )
    response.raise_for_status()
