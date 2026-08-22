"""LINE Messaging API のプッシュ送信。

LINE Notify は 2025年3月末で終了したため、Messaging API を使う。
LINE Developers で Messaging API チャネルを作り、チャネルアクセストークン
（長期）を取得して LINE_CHANNEL_ACCESS_TOKEN に入れる。
手順は docs/line-setup.md を参照。
"""

from __future__ import annotations

from typing import Any

import requests

API_BASE = "https://api.line.me/v2/bot"
PUSH_URL = f"{API_BASE}/message/push"
BROADCAST_URL = f"{API_BASE}/message/broadcast"
INFO_URL = f"{API_BASE}/info"
QUOTA_URL = f"{API_BASE}/message/quota"
CONSUMPTION_URL = f"{API_BASE}/message/quota/consumption"

TIMEOUT = 20
MAX_LENGTH = 4900   # 1メッセージ5000字の制限に余裕をもたせる
MAX_MESSAGES = 5    # 1リクエストにつきメッセージ5件まで

# 詰まりやすいところは、APIの生エラーではなく直し方を出す
HINTS = {
    401: "チャネルアクセストークンが無効か期限切れです。"
         "LINE Developers →「Messaging API設定」で再発行してください。",
    403: "このチャネルにメッセージ送信の権限がありません。"
         "Messaging APIチャネル（LINEログインチャネルではない）か確認してください。",
    429: "今月の無料送信数を使い切っています。"
         "LINE Official Account Manager で利用状況とプランを確認してください。",
}


class LineError(RuntimeError):
    pass


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _token(settings: dict[str, Any]) -> str:
    token = (settings.get("channel_access_token") or "").strip()
    if not token:
        raise LineError(
            "line.channel_access_token が未設定です"
            "（環境変数 LINE_CHANNEL_ACCESS_TOKEN を設定してください）"
        )
    return token


def _check(response: requests.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        hint = HINTS.get(response.status_code)
        detail = response.text[:300]
        raise LineError(
            f"LINE API {response.status_code}: {detail}" + (f"\n  → {hint}" if hint else "")
        )
    return response.json() if response.content else {}


def _chunks(text: str) -> list[str]:
    return [text[i:i + MAX_LENGTH] for i in range(0, len(text), MAX_LENGTH)] or [""]


def send(settings: dict[str, Any], subject: str, body: str) -> None:
    token = _token(settings)
    to = (settings.get("to") or "").strip()

    messages = [{"type": "text", "text": chunk} for chunk in _chunks(f"{subject}\n\n{body}")]
    payload: dict[str, Any] = {"messages": messages[:MAX_MESSAGES]}

    url = BROADCAST_URL
    if to:
        payload["to"] = to
        url = PUSH_URL

    _check(requests.post(url, headers=_headers(token), json=payload, timeout=TIMEOUT))


# ---------------------------------------------------------------- 設定の確認用
def bot_info(settings: dict[str, Any]) -> dict[str, Any]:
    """トークンが有効か確かめ、Botの表示名などを返す。"""
    return _check(requests.get(INFO_URL, headers=_headers(_token(settings)), timeout=TIMEOUT))


def quota(settings: dict[str, Any]) -> dict[str, Any]:
    """今月の送信上限と、すでに使った数を返す。

    上限はプランによって変わるため、数字は決め打ちせず API から取る。
    """
    headers = _headers(_token(settings))
    limit = _check(requests.get(QUOTA_URL, headers=headers, timeout=TIMEOUT))
    used = _check(requests.get(CONSUMPTION_URL, headers=headers, timeout=TIMEOUT))
    return {
        "type": limit.get("type"),          # "limited" なら上限あり、"none" なら無制限
        "limit": limit.get("value"),
        "used": used.get("totalUsage"),
    }
