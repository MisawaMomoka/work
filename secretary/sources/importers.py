"""他の家計簿アプリのCSVを取り込む。

OsidOri / Zaim / マネーフォワード / 銀行の明細など、日本の家計簿CSVは
列名も文字コードも符号の向きもバラバラなので、決め打ちせずに推測する。
推測結果は取り込み前に画面に出して、人が確認できるようにしている。
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .money import Expense

# 日本の家計簿CSVでよく使われる列名。左のキーに寄せる。
COLUMN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": ("日付", "取引日", "利用日", "計上日", "日時", "年月", "date", "年月日"),
    "amount": ("金額", "支出", "出金", "支払金額", "利用金額", "出金金額",
               "amount", "支払額", "引落金額"),
    "category": ("カテゴリ", "カテゴリー", "大項目", "費目", "分類", "category",
                 "支出カテゴリ", "項目"),
    "subcategory": ("中項目", "小項目", "サブカテゴリ", "サブカテゴリー"),
    "memo": ("メモ", "内容", "摘要", "店名", "備考", "説明", "memo", "取引内容", "品目"),
    "kind": ("収支", "種別", "区分", "入出金", "収支区分", "type"),
}

# 家計の「使ったお金」ではないので既定で除外する
EXCLUDED_PATTERNS = ("振替", "振込", "口座間", "transfer")

INCOME_WORDS = ("収入", "入金", "給与", "income")
EXPENSE_WORDS = ("支出", "出金", "支払", "expense")

ENCODINGS = ("utf-8-sig", "cp932", "utf-8", "euc_jp")


class ImportError_(Exception):
    """取り込み時のエラー（組み込みの ImportError と紛らわしいので別名）。"""


@dataclass
class ImportPlan:
    """CSVをどう解釈したか。取り込み前にこれを見せる。"""

    path: Path
    encoding: str
    headers: list[str]
    mapping: dict[str, str] = field(default_factory=dict)
    sign_rule: str = ""
    expenses: list[Expense] = field(default_factory=list)
    skipped_income: int = 0
    skipped_transfer: int = 0
    skipped_broken: int = 0
    duplicates: int = 0

    @property
    def total(self) -> int:
        return sum(e.amount for e in self.expenses)


# ---------------------------------------------------------------- 下ごしらえ
def _normalize(text: str) -> str:
    """全角/半角、大文字小文字、空白、BOMの違いを吸収する。"""
    cleaned = unicodedata.normalize("NFKC", text or "").strip().lstrip("﻿")
    return cleaned.replace(" ", "").replace("　", "").lower()


def read_text(path: str | Path) -> tuple[str, str]:
    """文字コードを推測して読む。読めた本文とエンコーディング名を返す。"""
    raw = Path(path).read_bytes()
    for encoding in ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        # cp932 として読めても中身が化けている場合があるので、簡単に検査する
        if "�" not in text:
            return text, encoding
    raise ImportError_(
        "文字コードを判別できませんでした。"
        "Excelで開いて『CSV UTF-8』として保存し直すと読めることがあります。"
    )


def detect_columns(headers: Iterable[str]) -> dict[str, str]:
    """ヘッダー行から、意味 → 実際の列名 の対応を推測する。"""
    normalized = {_normalize(h): h for h in headers if h}
    mapping: dict[str, str] = {}

    for key, synonyms in COLUMN_SYNONYMS.items():
        # まず完全一致
        for synonym in synonyms:
            hit = normalized.get(_normalize(synonym))
            if hit:
                mapping[key] = hit
                break
        if key in mapping:
            continue
        # 次に部分一致（「利用金額(円)」のような列に対応）
        for synonym in synonyms:
            needle = _normalize(synonym)
            for norm, original in normalized.items():
                if needle in norm and original not in mapping.values():
                    mapping[key] = original
                    break
            if key in mapping:
                break
    return mapping


def parse_date(value: str) -> date | None:
    """2026-08-22 / 2026/8/22 / 2026年8月22日 / 2026-08（月次）を受ける。"""
    text = unicodedata.normalize("NFKC", (value or "").strip())
    if not text:
        return None
    text = re.sub(r"[年月]", "/", text).replace("日", "").strip().rstrip("/")
    text = text.split(" ")[0]  # 時刻付きは日付だけ使う

    for pattern in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m", "%Y-%m", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text, pattern).date()
        except ValueError:
            continue
        return parsed
    return None


def parse_amount(value: str) -> int | None:
    """¥1,234 / -1,234 / 1234円 / (1,234) を整数にする。負号は保つ。"""
    text = unicodedata.normalize("NFKC", (value or "").strip())
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")   # 会計形式の(1,234)
    text = re.sub(r"[¥￥,、円\s()]", "", text)
    if not text or text in ("-", "+"):
        return None
    try:
        amount = int(round(float(text)))
    except ValueError:
        return None
    return -abs(amount) if negative else amount


def _is_excluded(*fields: str) -> bool:
    blob = _normalize(" ".join(f for f in fields if f))
    return any(_normalize(word) in blob for word in EXCLUDED_PATTERNS)


def _kind_of(text: str) -> str:
    """収支列の値から income / expense / unknown を判定する。"""
    blob = _normalize(text)
    if not blob:
        return "unknown"
    if any(_normalize(w) in blob for w in INCOME_WORDS):
        return "income"
    if any(_normalize(w) in blob for w in EXPENSE_WORDS):
        return "expense"
    return "unknown"


# ---------------------------------------------------------------- 本体
def build_plan(
    path: str | Path,
    existing: Iterable[Expense] = (),
    overrides: dict[str, str] | None = None,
    include_transfers: bool = False,
) -> ImportPlan:
    """CSVを読んで、取り込む内容を組み立てる（まだ書き込まない）。"""
    file = Path(path)
    if not file.is_file():
        raise ImportError_(f"ファイルが見つかりません: {file}")

    text, encoding = read_text(file)
    reader = csv.DictReader(io.StringIO(text))
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    if not headers:
        raise ImportError_("ヘッダー行が読めませんでした。CSVの1行目を確認してください。")

    mapping = detect_columns(headers)
    mapping.update({k: v for k, v in (overrides or {}).items() if v})

    for required in ("date", "amount"):
        if required not in mapping:
            label = "日付" if required == "date" else "金額"
            raise ImportError_(
                f"{label}の列を見つけられませんでした。\n"
                f"  読み取れた列: {', '.join(headers)}\n"
                f"  --{required}-col で列名を直接指定してください。"
            )

    rows = list(reader)
    plan = ImportPlan(path=file, encoding=encoding, headers=headers, mapping=mapping)

    # 符号の向きを決める（アプリによって支出がプラスだったりマイナスだったり）
    amounts = [parse_amount(r.get(mapping["amount"], "")) for r in rows]
    amounts = [a for a in amounts if a is not None]
    has_kind_column = "kind" in mapping
    has_negative = any(a < 0 for a in amounts)

    if has_kind_column:
        plan.sign_rule = "収支列で判定"
    elif has_negative:
        plan.sign_rule = "マイナスの行を支出として取り込み"
    else:
        plan.sign_rule = "全行を支出として取り込み"

    seen = {(e.date, e.category, e.amount, e.memo) for e in existing}

    for row in rows:
        day = parse_date(row.get(mapping["date"], ""))
        amount = parse_amount(row.get(mapping["amount"], ""))
        if day is None or amount is None or amount == 0:
            plan.skipped_broken += 1
            continue

        category = (row.get(mapping.get("category", ""), "") or "").strip()
        sub = (row.get(mapping.get("subcategory", ""), "") or "").strip()
        memo = (row.get(mapping.get("memo", ""), "") or "").strip()
        kind_text = (row.get(mapping.get("kind", ""), "") or "").strip()

        if not include_transfers and _is_excluded(category, sub, memo, kind_text):
            plan.skipped_transfer += 1
            continue

        # 収入は家計の支出ではないので入れない
        kind = _kind_of(kind_text) if has_kind_column else "unknown"
        if kind == "income":
            plan.skipped_income += 1
            continue
        if kind == "unknown" and has_negative and amount > 0:
            plan.skipped_income += 1
            continue

        expense = Expense(
            day,
            category or sub or "その他",
            abs(amount),
            " / ".join(p for p in (sub, memo) if p) if sub != category else memo,
        )
        key = (expense.date, expense.category, expense.amount, expense.memo)
        if key in seen:
            plan.duplicates += 1
            continue
        seen.add(key)
        plan.expenses.append(expense)

    plan.expenses.sort(key=lambda e: e.date)
    return plan


def describe(plan: ImportPlan, currency: str = "円") -> str:
    """取り込み前に人が確認するための要約。"""
    lines = [
        f"ファイル   : {plan.path.name}",
        f"文字コード : {plan.encoding}",
        f"読み取れた列: {', '.join(plan.headers)}",
        "",
        "列の対応（推測）:",
    ]
    labels = {
        "date": "日付", "amount": "金額", "category": "カテゴリ",
        "subcategory": "中項目", "memo": "メモ", "kind": "収支",
    }
    for key, label in labels.items():
        column = plan.mapping.get(key)
        lines.append(f"  {label:<6} ← {column}" if column else f"  {label:<6} ← （なし）")

    lines += [
        f"  符号の扱い : {plan.sign_rule}",
        "",
        f"取り込む件数: {len(plan.expenses)}件  合計 {plan.total:,}{currency}",
    ]
    if plan.expenses:
        first, last = plan.expenses[0].date, plan.expenses[-1].date
        lines.append(f"期間        : {first} 〜 {last}")

    skipped = [
        (plan.skipped_income, "収入・入金"),
        (plan.skipped_transfer, "振替・振込"),
        (plan.duplicates, "すでに取り込み済み"),
        (plan.skipped_broken, "日付か金額が読めない行"),
    ]
    for count, label in skipped:
        if count:
            lines.append(f"  除外 {count}件: {label}")

    if plan.expenses:
        lines += ["", "先頭5件:"]
        for expense in plan.expenses[:5]:
            memo = f"  {expense.memo}" if expense.memo else ""
            lines.append(
                f"  {expense.date}  {expense.category:<10} {expense.amount:>9,}{currency}{memo}"
            )
    return "\n".join(lines)


def apply_plan(plan: ImportPlan, expenses_file: str | Path) -> int:
    """実際に expenses.csv へ追記する。追記した件数を返す。"""
    from .money import append_expense

    for expense in plan.expenses:
        append_expense(expenses_file, expense)
    return len(plan.expenses)
