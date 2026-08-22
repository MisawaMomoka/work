"""Discord Webhook。"""

from __future__ import annotations

from typing import Any

import requests

TIMEOUT = 20
MAX_LENGTH = 1900  # Discordの2000字制限に少し余裕をもたせる


def send(settings: dict[str, Any], subject: str, body: str) -> None:
    url = settings.get("webhook_url")
    if not url:
        raise ValueError("discord.webhook_url が未設定です")
    content = f"**{subject}**\n{body}"
    for start in range(0, len(content), MAX_LENGTH):
        response = requests.post(
            url, json={"content": content[start:start + MAX_LENGTH]}, timeout=TIMEOUT
        )
        response.raise_for_status()
