"""設定ファイルの読み込み。

``${ENV_NAME}`` 形式の文字列は環境変数に展開する。未設定の環境変数は
空文字になり、その項目は「未設定」として扱われる（落ちない）。
"""

from __future__ import annotations

import os
import re
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

DEFAULT_CONFIG_PATHS = ("config.local.yaml", "config.yaml")


class ConfigError(Exception):
    pass


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value).strip()
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def _parse_hhmm(text: str) -> time:
    hour, _, minute = text.partition(":")
    return time(int(hour), int(minute or 0))


class Config:
    def __init__(self, raw: dict[str, Any], source: Path | None = None):
        self.raw = raw
        self.source = source

    # -- 便利アクセサ --------------------------------------------------
    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name) or {}
        if not isinstance(value, dict):
            raise ConfigError(f"設定 '{name}' は辞書である必要があります")
        return value

    @property
    def profile(self) -> dict[str, Any]:
        return self.section("profile")

    @property
    def tz(self) -> ZoneInfo:
        name = self.profile.get("timezone") or "Asia/Tokyo"
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:
            # Windows には tz データベースが無いので、tzdata が要る
            raise ConfigError(
                f"タイムゾーン '{name}' を解決できませんでした。"
                "次を実行してください: python -m pip install tzdata"
            ) from exc

    @property
    def work_hours(self) -> tuple[time, time]:
        hours = self.profile.get("work_hours") or ["09:00", "18:00"]
        return _parse_hhmm(hours[0]), _parse_hhmm(hours[1])

    @property
    def calendar(self) -> dict[str, Any]:
        return self.section("calendar")

    @property
    def tasks(self) -> dict[str, Any]:
        return self.section("tasks")

    @property
    def money(self) -> dict[str, Any]:
        return self.section("money")

    @property
    def notify(self) -> dict[str, Any]:
        return self.section("notify")

    @property
    def ai(self) -> dict[str, Any]:
        return self.section("ai")


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """設定を読む。パス未指定なら config.local.yaml → config.yaml の順で探す。"""
    candidates = [Path(path)] if path else [Path(p) for p in DEFAULT_CONFIG_PATHS]
    for candidate in candidates:
        if candidate.is_file():
            raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise ConfigError(f"{candidate} のトップレベルは辞書である必要があります")
            return Config(_expand(raw), candidate)
    searched = ", ".join(str(c) for c in candidates)
    raise ConfigError(f"設定ファイルが見つかりません（探した場所: {searched}）")
