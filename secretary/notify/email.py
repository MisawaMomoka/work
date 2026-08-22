"""SMTPでメール送信（Gmailなら「アプリパスワード」を使う）。"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

TIMEOUT = 30


def send(settings: dict[str, Any], subject: str, body: str) -> None:
    host = settings.get("smtp_host")
    username = settings.get("username")
    password = settings.get("password")
    from_addr = settings.get("from_addr") or username
    to_addrs = [addr for addr in (settings.get("to_addrs") or []) if addr]

    if not (host and from_addr and to_addrs):
        raise ValueError("email の smtp_host / from_addr / to_addrs が未設定です")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = ", ".join(to_addrs)
    message.set_content(body)

    port = int(settings.get("smtp_port") or 587)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT) as server:
            if username and password:
                server.login(username, password)
            server.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=TIMEOUT) as server:
        server.starttls()
        if username and password:
            server.login(username, password)
        server.send_message(message)
