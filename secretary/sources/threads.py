"""Threads（Meta）の投稿をまとめて取り出し、CSVに書き出す。

対象は「自分以外の公開アカウント」。公式の Threads API には
**Profile Discovery** という枠があり、公開プロフィールを username で引いて、
そのアカウントの投稿一覧を取得できる。ただし条件がある。

  * Meta のアプリ審査で ``threads_profile_discovery`` の承認が要る
  * 相手が公開アカウントで、フォロワーが一定数以上（2025年11月時点で100人）
  * 1日あたりのリクエスト数に上限がある（同時点で1,000回/24時間）

審査前でも ``threads_basic`` だけで自分のアカウントは引けるので、
まず ``threads-test`` で疎通を確かめてから本番を回す作りにしている。

スクレイピングは使わない。Threadsの規約違反になるうえ、
画面が変わるたびに壊れるため（README の銀行連携と同じ判断）。
"""

from __future__ import annotations

import csv
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

# 公式APIの入り口。将来パスが変わったら config.yaml の threads.api_base で上書きできる。
DEFAULT_API_BASE = "https://graph.threads.net/v1.0"

# username からプロフィール（＝数値のユーザーID）を引くエンドポイント。
PROFILE_LOOKUP_PATH = "profile_lookup"

# そのユーザーIDの投稿一覧。config.yaml の threads.posts_path で上書きできる。
DEFAULT_POSTS_PATH = "{user_id}/threads"

TIMEOUT = 30
PAGE_SIZE = 100          # APIの上限
MAX_PAGES = 200          # 暴走よけ。100件×200ページ＝2万件で打ち止め
PAGE_INTERVAL = 0.3      # 連打しない

PROFILE_FIELDS = [
    "id",
    "username",
    "name",
    "threads_profile_picture_url",
    "threads_biography",
    "is_verified",
]

# 取れるなら取りたい欄。存在しない欄を指定するとAPIが400を返すので、
# 怒られた欄だけ落として自動で取り直す（_get_with_field_fallback）。
CORE_POST_FIELDS = [
    "id",
    "username",
    "text",
    "timestamp",
    "permalink",
    "media_type",
]
EXTRA_POST_FIELDS = [
    "media_url",
    "shortcode",
    "is_quote_post",
    "has_replies",
    "is_reply",
    "replied_to",
    "root_post",
    "quoted_post",
    "reposted_post",
    "link_attachment_url",
    "topic_tag",
]

FIELDNAMES = [
    "id",
    "posted_at",
    "username",
    "type",
    "text",
    "media_type",
    "permalink",
    "media_url",
    "link_attachment_url",
    "topic_tag",
    "replied_to_id",
    "root_post_id",
    "timestamp",
    "fetched_at",
]

# APIの生エラーではなく「次に何をすればいいか」を出す
ERROR_HINTS = {
    190: "アクセストークンが無効か期限切れです。Meta for Developers で取り直してください（手順: docs/threads-export.md）。",
    102: "アクセストークンが無効か期限切れです。取り直してください。",
    10: "この権限ではこのデータを取れません。threads_profile_discovery のアプリ審査が必要です。",
    200: "この権限ではこのデータを取れません。threads_profile_discovery のアプリ審査が必要です。",
    4: "リクエストの上限に達しました。24時間ほど空けてから再実行してください。",
    17: "リクエストの上限に達しました。24時間ほど空けてから再実行してください。",
    32: "リクエストの上限に達しました。24時間ほど空けてから再実行してください。",
    80007: "Threads APIの利用上限に達しました。しばらく待ってから再実行してください。",
}

_NONEXISTENT_FIELD = re.compile(r"nonexist(?:ing|ent) field \(([^)]+)\)")


class ThreadsError(RuntimeError):
    pass


# ---------------------------------------------------------------- 設定
def settings_from(config_section: dict[str, Any] | None) -> dict[str, Any]:
    """config.yaml の threads: セクションを、既定値で埋めて返す。"""
    section = dict(config_section or {})
    section.setdefault("api_base", DEFAULT_API_BASE)
    section.setdefault("posts_path", DEFAULT_POSTS_PATH)
    section.setdefault("out_file", "data/threads_posts.csv")
    section.setdefault("include_replies", True)
    return section


def _token(settings: dict[str, Any]) -> str:
    token = str(settings.get("access_token") or "").strip()
    if not token:
        raise ThreadsError(
            "threads.access_token が未設定です"
            "（環境変数 THREADS_ACCESS_TOKEN を設定してください / 手順: docs/threads-export.md）"
        )
    return token


def normalize_username(name: str) -> str:
    """@つき・URL貼り付け・空白まじり、どれで渡されても username だけにする。"""
    text = (name or "").strip()
    if not text:
        return ""
    if "threads.net/" in text or "threads.com/" in text:
        text = text.split("threads.net/")[-1].split("threads.com/")[-1]
        text = text.split("/")[0].split("?")[0]
    return text.lstrip("@").strip()


# ---------------------------------------------------------------- HTTP
def _describe_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else {}
    error = error if isinstance(error, dict) else {}

    message = str(error.get("message") or response.text[:200] or "(本文なし)")
    code = error.get("code")
    hint = ERROR_HINTS.get(code)
    if hint is None and response.status_code == 429:
        hint = ERROR_HINTS[4]
    if hint is None and response.status_code in (401, 403):
        hint = ERROR_HINTS[10]

    detail = f"HTTP {response.status_code}: {message}"
    return detail + (f"\n  → {hint}" if hint else "")


def _get(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise ThreadsError(f"接続できませんでした: {type(exc).__name__}") from exc

    if response.status_code >= 400:
        raise ThreadsError(_describe_error(response))

    try:
        payload = response.json()
    except ValueError as exc:
        raise ThreadsError("APIの応答がJSONではありませんでした") from exc
    if not isinstance(payload, dict):
        raise ThreadsError("APIの応答の形式が想定と違います")
    return payload


def _get_with_field_fallback(url: str, params: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """欄（fields）が多すぎて400になったら、怒られた欄を外して取り直す。

    Threads API は仕様追加が多く、こちらが知っている欄が相手に無いことがある。
    そのたびに全部落ちるより、取れる欄だけで前に進むほうがいい。
    """
    remaining = list(fields)
    for _ in range(len(EXTRA_POST_FIELDS) + 1):
        try:
            return _get(url, {**params, "fields": ",".join(remaining)})
        except ThreadsError as exc:
            unknown = {f.strip() for f in _NONEXISTENT_FIELD.findall(str(exc))}
            droppable = unknown & set(remaining)
            if not droppable:
                raise
            remaining = [f for f in remaining if f not in droppable]
            if not remaining:
                raise
            logger.warning("このAPIに無い欄を外して取り直します: %s", ", ".join(sorted(droppable)))
    raise ThreadsError("欄の指定を調整しきれませんでした")


# ---------------------------------------------------------------- API
def lookup_profile(settings: dict[str, Any], username: str) -> dict[str, Any]:
    """username から公開プロフィール（ユーザーIDを含む）を引く。"""
    name = normalize_username(username)
    if not name:
        raise ThreadsError("ユーザー名が空です（例: python -m secretary threads @zuck）")

    base = str(settings.get("api_base") or DEFAULT_API_BASE).rstrip("/")
    url = f"{base}/{PROFILE_LOOKUP_PATH}"
    params = {"username": name, "access_token": _token(settings)}

    try:
        profile = _get_with_field_fallback(url, params, list(PROFILE_FIELDS))
    except ThreadsError as exc:
        raise ThreadsError(
            f"@{name} のプロフィールを取得できませんでした。\n{exc}\n"
            "  → 非公開アカウント、フォロワーが少ない（100人未満）、"
            "ユーザー名の綴り違い、のいずれかでも同じエラーになります。"
        ) from exc

    if not profile.get("id"):
        raise ThreadsError(f"@{name} のユーザーIDが取得できませんでした（応答に id がありません）")
    return profile


def _posts_url(settings: dict[str, Any], user_id: str) -> str:
    base = str(settings.get("api_base") or DEFAULT_API_BASE).rstrip("/")
    path = str(settings.get("posts_path") or DEFAULT_POSTS_PATH).format(user_id=user_id)
    return f"{base}/{path.lstrip('/')}"


def _unix(day: date, tz: ZoneInfo) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=tz).timestamp())


def iter_posts(
    settings: dict[str, Any],
    user_id: str,
    *,
    tz: ZoneInfo,
    since: date | None = None,
    until: date | None = None,
    max_pages: int = MAX_PAGES,
) -> Iterator[dict[str, Any]]:
    """投稿を古い順・新しい順を問わずページングで全部たどる。"""
    fields = CORE_POST_FIELDS + EXTRA_POST_FIELDS
    params: dict[str, Any] = {
        "access_token": _token(settings),
        "limit": PAGE_SIZE,
    }
    if since:
        params["since"] = _unix(since, tz)
    if until:
        # until は「その日まで含める」ほうが直感に合うので、翌日0時の直前にする
        params["until"] = _unix(until, tz) + 86399

    url = _posts_url(settings, user_id)
    payload = _get_with_field_fallback(url, params, fields)

    for page in range(1, max_pages + 1):
        data = payload.get("data")
        if not isinstance(data, list):
            raise ThreadsError("投稿一覧の応答に data がありません")
        for item in data:
            if isinstance(item, dict):
                yield item

        next_url = ((payload.get("paging") or {}).get("next")) if isinstance(payload.get("paging"), dict) else None
        if not next_url:
            return
        if page >= max_pages:
            logger.warning("%d ページで打ち切りました（--max-pages で増やせます）", max_pages)
            return
        time.sleep(PAGE_INTERVAL)
        payload = _get(next_url)


# ---------------------------------------------------------------- 整形
def _ref_id(value: Any) -> str:
    """replied_to / root_post は {"id": "..."} で返る。IDだけ取り出す。"""
    if isinstance(value, dict):
        return str(value.get("id") or "")
    if isinstance(value, (str, int)):
        return str(value)
    return ""


def classify(post: dict[str, Any]) -> str:
    """スプレッドシートで絞り込みやすいように、日本語の種別を付ける。"""
    if post.get("reposted_post"):
        return "リポスト"
    if post.get("is_quote_post") or post.get("quoted_post"):
        return "引用"
    if post.get("is_reply") or post.get("replied_to"):
        return "返信"
    return "投稿"


def to_row(post: dict[str, Any], tz: ZoneInfo, fetched_at: datetime | None = None) -> dict[str, str]:
    raw_timestamp = str(post.get("timestamp") or "")
    posted_at = ""
    if raw_timestamp:
        try:
            moment = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            posted_at = moment.astimezone(tz).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            posted_at = ""

    stamp = fetched_at or datetime.now(tz)
    return {
        "id": str(post.get("id") or ""),
        "posted_at": posted_at,
        "username": str(post.get("username") or ""),
        "type": classify(post),
        "text": str(post.get("text") or ""),
        "media_type": str(post.get("media_type") or ""),
        "permalink": str(post.get("permalink") or ""),
        "media_url": str(post.get("media_url") or ""),
        "link_attachment_url": str(post.get("link_attachment_url") or ""),
        "topic_tag": str(post.get("topic_tag") or ""),
        "replied_to_id": _ref_id(post.get("replied_to")),
        "root_post_id": _ref_id(post.get("root_post")),
        "timestamp": raw_timestamp,
        "fetched_at": stamp.strftime("%Y-%m-%d %H:%M"),
    }


# ---------------------------------------------------------------- CSV
def load_rows(path: str | Path) -> list[dict[str, str]]:
    """すでに書き出したCSVを読む（差分更新のため）。無ければ空。"""
    file = Path(path)
    if not file.is_file():
        return []
    # Excel向けにBOM付きで書いているので、utf-8-sig で読めば両方いける
    with file.open(encoding="utf-8-sig", newline="") as handle:
        return [
            {key: (value or "") for key, value in row.items() if key in FIELDNAMES}
            for row in csv.DictReader(handle)
            if (row.get("id") or "").strip()
        ]


def merge_rows(existing: Iterable[dict[str, str]], fresh: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """IDで重複を消し、新しい投稿が上に来るように並べ替える。

    同じIDなら新しく取ったほう（本文の編集が反映されている）を採る。
    """
    merged: dict[str, dict[str, str]] = {}
    for row in existing:
        key = (row.get("id") or "").strip()
        if key:
            merged[key] = row
    for row in fresh:
        key = (row.get("id") or "").strip()
        if key:
            merged[key] = row
    return sorted(
        merged.values(),
        key=lambda r: (r.get("timestamp") or "", r.get("id") or ""),
        reverse=True,
    )


def write_csv(path: str | Path, rows: Iterable[dict[str, str]]) -> int:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    # utf-8-sig: WindowsのExcelでそのまま開いても日本語が化けない
    with file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in FIELDNAMES})
            count += 1
    return count


# ---------------------------------------------------------------- まとめ役
class ExportResult:
    __slots__ = ("profile", "rows", "fetched", "added", "total")

    def __init__(self, profile: dict[str, Any], rows: list[dict[str, str]],
                 fetched: int, added: int):
        self.profile = profile
        self.rows = rows
        self.fetched = fetched      # APIから取った件数（絞り込み前）
        self.added = added          # CSVに新しく増えた件数
        self.total = len(rows)      # CSVに入る合計件数


def export(
    settings: dict[str, Any],
    username: str,
    *,
    tz: ZoneInfo,
    since: date | None = None,
    until: date | None = None,
    include_replies: bool = True,
    fresh: bool = False,
    max_pages: int = MAX_PAGES,
    out_file: str | Path | None = None,
) -> ExportResult:
    """@username の投稿を取ってきて、CSVに入れる行まで作る（書き込みはしない）。"""
    profile = lookup_profile(settings, username)
    path = out_file or settings.get("out_file") or "data/threads_posts.csv"

    fetched_at = datetime.now(tz)
    fetched = 0
    fresh_rows: list[dict[str, str]] = []
    for post in iter_posts(
        settings, str(profile["id"]), tz=tz, since=since, until=until, max_pages=max_pages
    ):
        fetched += 1
        row = to_row(post, tz, fetched_at)
        if not include_replies and row["type"] == "返信":
            continue
        fresh_rows.append(row)

    existing = [] if fresh else load_rows(path)
    known = {(row.get("id") or "").strip() for row in existing}
    added = sum(1 for row in fresh_rows if row["id"] not in known)

    return ExportResult(profile, merge_rows(existing, fresh_rows), fetched, added)
