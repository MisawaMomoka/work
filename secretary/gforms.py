"""Google フォームを読み取り、回答を送信する。

フォームのページには質問の一覧が ``FB_PUBLIC_LOAD_DATA_`` という JavaScript の
変数で埋め込まれている。これを読めば、各質問の
「見出し・種類・選択肢・必須かどうか・送信用の entry ID」がわかる。
送信は ``/formResponse`` に ``entry.<ID>=回答`` を POST するだけでよい。

Googleログインが必要なフォームとファイルのアップロードには対応していない。
グリッド形式と時刻の質問にも対応していない（その場合は送信せず、本人に回す）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (compatible; ai-secretary)"

# 質問の種類（Googleフォーム内部の番号）
TEXT, PARAGRAPH, CHOICE, DROPDOWN, CHECKBOX, SCALE, GRID, PAGE_BREAK, DATE, TIME, FILE = (
    0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 13,
)
KIND_NAMES = {
    TEXT: "記述（短文）", PARAGRAPH: "記述（段落）", CHOICE: "ラジオボタン（1つ選ぶ）",
    DROPDOWN: "プルダウン（1つ選ぶ）", CHECKBOX: "チェックボックス（複数選べる）",
    SCALE: "均等目盛", DATE: "日付", GRID: "グリッド", TIME: "時刻", FILE: "ファイルのアップロード",
}
SUPPORTED = {TEXT, PARAGRAPH, CHOICE, DROPDOWN, CHECKBOX, SCALE, DATE}
# 見出し・説明文・画像・動画など、回答が要らないもの
DISPLAY_ONLY = {6, PAGE_BREAK, 11, 12}

CONFIRMATION_MARKERS = (
    "freebirdFormviewerViewResponseConfirmationMessage",
    "回答を記録しました",
    "Your response has been recorded",
)

FORM_URL_PATTERN =re.compile(r"https?://(?:docs\.google\.com/forms/[^\s<>\"')]+|forms\.gle/[A-Za-z0-9]+)")


class FormError(Exception):
    pass


@dataclass
class Question:
    entry_id: str
    title: str
    description: str
    kind: int
    required: bool
    options: list[str] = field(default_factory=list)
    allows_other: bool = False

    @property
    def kind_name(self) -> str:
        return KIND_NAMES.get(self.kind, f"不明({self.kind})")

    @property
    def supported(self) -> bool:
        return self.kind in SUPPORTED


@dataclass
class Form:
    url: str              # viewform のURL（クエリなし）
    title: str
    description: str
    questions: list[Question]
    page_count: int
    prefilled: dict[str, str]   # メールのリンクにあらかじめ入っていた entry（応募者IDなど）
    collects_email: bool
    fbzx: str = ""

    @property
    def response_url(self) -> str:
        return re.sub(r"/viewform$", "/formResponse", self.url)


# ------------------------------------------------------------------ 読み取り
def find_form_url(text: str, marker: str | None = None) -> str | None:
    """本文からフォームのURLを探す。marker があれば、その後ろで最初に出てくるもの。"""
    if marker and marker in text:
        found = FORM_URL_PATTERN.search(text, text.index(marker))
        if found:
            return found.group(0)
    found = FORM_URL_PATTERN.search(text)
    return found.group(0) if found else None


def split_prefilled(url: str) -> tuple[str, dict[str, str]]:
    """URLを「クエリなしのURL」と「entry.* の事前入力」に分ける。"""
    parts = urlparse(url)
    prefilled = {k: v for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.startswith("entry.")}
    return urlunparse(parts._replace(query="", fragment="")), prefilled


def _load_data(html: str) -> list[Any]:
    match = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);\s*</script>", html, re.S)
    if not match:
        raise FormError("フォームの質問データが見つかりません（ログインが必要なフォームかもしれません）")
    return json.loads(match.group(1))


def parse_form(html: str, url: str) -> Form:
    data = _load_data(html)
    base, prefilled = split_prefilled(url)
    info = data[1] if len(data) > 1 and isinstance(data[1], list) else []
    items = info[1] if len(info) > 1 and isinstance(info[1], list) else []
    questions: list[Question] = []
    pages = 1
    for item in items:
        if not isinstance(item, list) or len(item) < 4:
            continue
        title, description, kind = item[1] or "", item[2] or "", item[3]
        if kind == PAGE_BREAK:
            pages += 1
        if kind in DISPLAY_ONLY:
            continue
        fields = item[4] if len(item) > 4 and isinstance(item[4], list) else []
        if not fields:
            continue
        if kind == GRID:
            # グリッドは行ごとに entry が分かれる。まとめて1問として扱い、未対応にする
            questions.append(Question(str(fields[0][0]), title, description, kind,
                                      any(bool(f[2]) for f in fields if len(f) > 2)))
            continue
        first = fields[0]
        options, allows_other = [], False
        for option in (first[1] if len(first) > 1 and isinstance(first[1], list) else []):
            if len(option) > 4 and option[4]:
                allows_other = True
            elif option and option[0] not in (None, ""):
                options.append(str(option[0]))
        required = bool(first[2]) if len(first) > 2 else False
        questions.append(Question(str(first[0]), title, description, kind, required, options, allows_other))
    fbzx = re.search(r'name="fbzx"\s+value="([^"]+)"', html)
    return Form(
        url=base,
        title=str(data[3] if len(data) > 3 and data[3] else (info[8] if len(info) > 8 else "") or ""),
        description=str(info[0] or "") if info else "",
        questions=questions,
        page_count=pages,
        prefilled=prefilled,
        collects_email='name="emailAddress"' in html,
        fbzx=fbzx.group(1) if fbzx else "",
    )


def fetch_form(url: str, session: requests.Session | None = None) -> Form:
    http = session or requests.Session()
    response = http.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
    if "accounts.google.com" in response.url:
        raise FormError("Googleアカウントでのログインが必要なフォームです（自動入力できません）")
    response.raise_for_status()
    # forms.gle の短縮URLは飛んだ先が本当のURL。事前入力は元のURLのものを優先する
    final_url = response.url
    form = parse_form(response.text, final_url)
    _, original_prefilled = split_prefilled(url)
    form.prefilled = {**form.prefilled, **original_prefilled}
    return form


# ------------------------------------------------------------------ 回答の検証
@dataclass
class Checked:
    fields: list[tuple[str, str]]   # 送信する (名前, 値) の並び
    problems: list[str]             # 送信できない理由（空なら送信してよい）


def _normalize(text: str) -> str:
    import unicodedata

    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def _match_option(value: str, options: list[str]) -> str | None:
    if value in options:
        return value
    wanted = _normalize(value)
    for option in options:
        if _normalize(option) == wanted:
            return option
    return None


def check_answers(form: Form, answers: dict[str, Any], email: str = "") -> Checked:
    """AIの回答をフォームの決まりに照らして確かめ、送信用の値に直す。

    answers は {entry_id: 値}。値は文字列、チェックボックスなら文字列のリスト。
    None や空は「答えられない」。
    """
    fields: list[tuple[str, str]] = []
    problems: list[str] = []
    for key, value in form.prefilled.items():
        fields.append((key, value))
    prefilled_ids = {key.split(".", 1)[1] for key in form.prefilled}

    for q in form.questions:
        if q.entry_id in prefilled_ids:
            continue
        raw = answers.get(q.entry_id)
        values = [str(v).strip() for v in (raw if isinstance(raw, list) else [raw]) if v not in (None, "") and str(v).strip()]
        if not values:
            if q.required:
                problems.append(f"必須の質問に答えられません: {q.title}")
            continue
        if not q.supported:
            problems.append(f"{q.kind_name}の質問には自動で答えられません: {q.title}")
            continue
        name = f"entry.{q.entry_id}"

        if q.kind in (TEXT, PARAGRAPH):
            fields.append((name, "\n".join(values) if q.kind == PARAGRAPH else " ".join(values)))
        elif q.kind in (CHOICE, DROPDOWN, SCALE):
            value = values[0]
            option = _match_option(value, q.options)
            if option is not None:
                fields.append((name, option))
            elif q.allows_other and q.kind == CHOICE:
                fields += [(name, "__other_option__"), (f"{name}.other_option_response", value)]
            else:
                problems.append(f"選択肢にない回答です: {q.title} →「{value}」")
        elif q.kind == CHECKBOX:
            others = []
            for value in values:
                option = _match_option(value, q.options)
                if option is not None:
                    fields.append((name, option))
                elif q.allows_other:
                    others.append(value)
                else:
                    problems.append(f"選択肢にない回答です: {q.title} →「{value}」")
            if others:
                fields += [(name, "__other_option__"), (f"{name}.other_option_response", "、".join(others))]
        elif q.kind == DATE:
            try:
                day = date.fromisoformat(values[0])
            except ValueError:
                problems.append(f"日付の形がおかしいです: {q.title} →「{values[0]}」")
                continue
            fields += [(f"{name}_year", str(day.year)), (f"{name}_month", str(day.month)),
                       (f"{name}_day", str(day.day))]

    if form.collects_email:
        if email:
            fields.append(("emailAddress", email))
        else:
            problems.append("このフォームはメールアドレスの入力欄があります（プロフィールにメールアドレスがありません）")
    return Checked(fields, problems)


# ------------------------------------------------------------------ 送信
def prefill_url(form: Form, fields: list[tuple[str, str]]) -> str:
    """回答を入れた状態で開けるURL。本人が開いて「送信」を押すだけで済む。"""
    usable = [(k, v) for k, v in fields if k.startswith("entry.") and not re.search(r"_(year|month|day)$", k)]
    dates: dict[str, dict[str, str]] = {}
    for k, v in fields:
        m = re.match(r"(entry\.\d+)_(year|month|day)$", k)
        if m:
            dates.setdefault(m.group(1), {})[m.group(2)] = v
    for k, parts in dates.items():
        usable.append((k, f"{parts['year']}-{int(parts['month']):02d}-{int(parts['day']):02d}"))
    return f"{form.url}?{urlencode([('usp', 'pp_url'), *usable])}"


def submit(form: Form, fields: list[tuple[str, str]], session: requests.Session | None = None) -> None:
    http = session or requests.Session()
    payload = list(fields)
    payload.append(("fvv", "1"))
    if form.page_count > 1:
        # 複数ページのフォームは「どのページを通ったか」も送る必要がある
        payload.append(("pageHistory", ",".join(str(i) for i in range(form.page_count))))
    if form.fbzx:
        payload += [("fbzx", form.fbzx), ("partialResponse", json.dumps([None, None, form.fbzx]))]
    response = http.post(
        form.response_url, data=payload, timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Referer": form.url},
    )
    if response.status_code != 200:
        raise FormError(f"送信に失敗しました（HTTP {response.status_code}）")
    confirmed = any(mark in response.text for mark in CONFIRMATION_MARKERS)
    if "FB_PUBLIC_LOAD_DATA_" in response.text and not confirmed:
        # 受付画面ではなくフォームが返ってきた = 入力エラーで差し戻された
        raise FormError("フォームに差し戻されました（必須項目の不足か、入力形式の誤り）")
