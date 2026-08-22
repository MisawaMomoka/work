"""標準出力。手元での動作確認用。"""

from __future__ import annotations

from typing import Any


def send(settings: dict[str, Any], subject: str, body: str) -> None:
    print(f"===== {subject} =====")
    print(body)
    print("=" * (len(subject) + 12))
