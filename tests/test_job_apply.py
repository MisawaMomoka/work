import json
from email.message import EmailMessage
from urllib.parse import parse_qsl, urlparse

import pytest

from secretary import gforms, job_apply

FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSabc/viewform"


def form_html(items, fbzx="-123", email=False):
    data = [None, ["フォームの説明", items, None, None, None, None, None, None, "応募フォーム"], "/forms", "SNS運用 応募フォーム"]
    hidden = '<input type="hidden" name="emailAddress">' if email else ""
    return (
        f'<html><script>var FB_PUBLIC_LOAD_DATA_ = {json.dumps(data, ensure_ascii=False)};</script>'
        f'<input type="hidden" name="fbzx" value="{fbzx}">{hidden}</html>'
    )


ITEMS = [
    [1, "応募者ID", None, 0, [[604283207, None, 1]]],
    [2, "お名前", None, 0, [[111, None, 1]]],
    [3, "自己PR", "300字程度", 1, [[222, None, 1]]],
    [4, "稼働可能時間", None, 2, [[333, [["週10時間未満"], ["週10〜20時間"], ["週20時間以上"], ["", None, None, None, True]], 1]]],
    [5, "対応可能な業務", None, 4, [[444, [["投稿作成"], ["動画編集"], ["分析"]], 0]]],
    [6, "次のページ", None, 8, None],
    [7, "稼働開始可能日", None, 9, [[555, None, 0]]],
    [8, "ポートフォリオ", None, 13, [[666, None, 0]]],
]


@pytest.fixture
def form():
    url = FORM_URL + "?usp=pp_url&entry.604283207=006dc00000sSsMXAA0_5587280"
    return gforms.parse_form(form_html(ITEMS), url)


# ------------------------------------------------------------------ フォームの読み取り
def test_parse_form(form):
    assert form.title == "SNS運用 応募フォーム"
    assert form.url == FORM_URL
    assert form.prefilled == {"entry.604283207": "006dc00000sSsMXAA0_5587280"}
    assert form.page_count == 2
    assert form.fbzx == "-123"
    by_id = {q.entry_id: q for q in form.questions}
    assert by_id["333"].options == ["週10時間未満", "週10〜20時間", "週20時間以上"]
    assert by_id["333"].allows_other and by_id["333"].required
    assert by_id["444"].kind == gforms.CHECKBOX and not by_id["444"].required
    assert not by_id["666"].supported


def test_find_form_url_after_marker():
    text = (
        "参考: https://docs.google.com/forms/d/e/OTHER/viewform\n"
        "▼応募はこちら\nhttps://docs.google.com/forms/d/e/1FAIpQLSabc/viewform?usp=pp_url&entry.1=x\n\n"
        "▼選考突破のコツ\nhttps://go.pardot.com/e/1082083/abc"
    )
    assert gforms.find_form_url(text, "▼応募はこちら").endswith("entry.1=x")
    assert gforms.find_form_url("リンク https://forms.gle/AbC123 です").endswith("AbC123")
    assert gforms.find_form_url("リンクなし") is None


def test_login_required_form_is_rejected():
    with pytest.raises(gforms.FormError):
        gforms.parse_form("<html>no data</html>", FORM_URL)


# ------------------------------------------------------------------ 回答の検証
GOOD = {
    "111": ["山田 花子"],
    "222": ["Instagramの立ち上げを3件担当しました。"],
    "333": ["週20時間以上"],
    "444": ["投稿作成", "分析"],
    "555": ["2026-11-01"],
}


def test_check_answers_builds_fields(form):
    checked = gforms.check_answers(form, GOOD)
    assert checked.problems == []
    fields = checked.fields
    assert ("entry.604283207", "006dc00000sSsMXAA0_5587280") in fields  # 応募者IDは触らない
    assert ("entry.444", "投稿作成") in fields and ("entry.444", "分析") in fields
    assert ("entry.555_year", "2026") in fields and ("entry.555_day", "1") in fields


def test_missing_required_blocks_submit(form):
    answers = {**GOOD, "222": []}
    assert any("自己PR" in p for p in gforms.check_answers(form, answers).problems)


def test_option_matching_and_other(form):
    checked = gforms.check_answers(form, {**GOOD, "333": ["週２０時間以上"]})  # 全角でも合わせる
    assert ("entry.333", "週20時間以上") in checked.fields
    checked = gforms.check_answers(form, {**GOOD, "333": ["応相談"]})
    assert ("entry.333", "__other_option__") in checked.fields
    assert ("entry.333.other_option_response", "応相談") in checked.fields
    checked = gforms.check_answers(form, {**GOOD, "444": ["営業"]})
    assert checked.problems  # 「その他」が無い質問で選択肢にない回答


def test_unsupported_question_answered_is_a_problem(form):
    assert gforms.check_answers(form, {**GOOD, "666": ["portfolio.pdf"]}).problems


def test_email_field_needs_address():
    form = gforms.parse_form(form_html(ITEMS[:2], email=True), FORM_URL)
    assert gforms.check_answers(form, {"111": ["山田"]}).problems
    checked = gforms.check_answers(form, {"111": ["山田"]}, email="me@example.com")
    assert ("emailAddress", "me@example.com") in checked.fields


def test_prefill_url(form):
    url = gforms.prefill_url(form, gforms.check_answers(form, GOOD).fields)
    query = parse_qsl(urlparse(url).query)
    assert ("entry.555", "2026-11-01") in query
    assert ("entry.604283207", "006dc00000sSsMXAA0_5587280") in query
    assert ("usp", "pp_url") in query


class FakeResponse:
    def __init__(self, status=200, text="<div>回答を記録しました</div>"):
        self.status_code = status
        self.text = text


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.posted = None

    def post(self, url, data, **kwargs):
        self.posted = (url, data)
        return self.response


def test_submit_posts_to_form_response(form):
    session = FakeSession(FakeResponse())
    gforms.submit(form, [("entry.111", "x")], session)
    url, data = session.posted
    assert url.endswith("/formResponse")
    assert ("pageHistory", "0,1") in data and ("fbzx", "-123") in data


def test_submit_detects_bounce(form):
    with pytest.raises(gforms.FormError):
        gforms.submit(form, [], FakeSession(FakeResponse(text=form_html(ITEMS))))
    with pytest.raises(gforms.FormError):
        gforms.submit(form, [], FakeSession(FakeResponse(status=400)))


# ------------------------------------------------------------------ メール
def mail(from_="ココナラアシスト運営 <assist@example.jp>", html=None, text=None):
    msg = EmailMessage()
    msg["From"] = from_
    msg["Subject"] = "【新着案件】SNSマーケティングディレクター"
    if text:
        msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html") if text else msg.set_content(html, subtype="html")
    return msg


def test_sender_check():
    settings = {"from_contains": ["ココナラアシスト"]}
    assert job_apply.is_from_sender(mail(), settings)
    assert not job_apply.is_from_sender(mail(from_="someone@example.com"), settings)
    # アドレスを指定したら、名前が同じでもアドレス違いは弾く（なりすまし対策）
    strict = {**settings, "from_addresses": ["assist@example.jp"]}
    assert not job_apply.is_from_sender(mail(from_="ココナラアシスト運営 <fake@evil.example>"), strict)


def test_html_body_keeps_link_urls():
    html = '<p>▼応募はこちら<br><a href="https://docs.google.com/forms/d/e/X/viewform?entry.1=a&amp;usp=pp_url">応募する</a></p>'
    text = job_apply.body_text(mail(html=html))
    assert "▼応募はこちら" in text
    assert gforms.find_form_url(text, "▼応募はこちら").endswith("entry.1=a&usp=pp_url")


# ------------------------------------------------------------------ 1通の処理
SETTINGS = {"from_contains": ["ココナラアシスト"], "auto_submit": True}
MAIL_TEXT = "案件の説明\n▼応募はこちら\n" + FORM_URL + "?entry.604283207=ID1\n"


@pytest.fixture
def stubs(monkeypatch, form):
    calls = {"submitted": None}
    monkeypatch.setattr(gforms, "fetch_form", lambda url: form)
    monkeypatch.setattr(gforms, "submit", lambda f, fields: calls.__setitem__("submitted", fields))
    state = {"judgement": job_apply.Judgement("SNS運用", True, "条件に合う", [], {k: v for k, v in GOOD.items()}, [])}
    monkeypatch.setattr(job_apply, "judge", lambda *a: state["judgement"])
    return calls, state


def test_process_submits_when_everything_is_fine(stubs):
    calls, _ = stubs
    outcome = job_apply.process(mail(text=MAIL_TEXT), SETTINGS, "経歴", "条件", "", dry_run=False)
    assert outcome.status == "applied"
    assert calls["submitted"] is not None


def test_process_without_auto_submit_sends_prefilled_link(stubs):
    calls, _ = stubs
    outcome = job_apply.process(mail(text=MAIL_TEXT), {**SETTINGS, "auto_submit": False}, "経歴", "条件", "", False)
    assert outcome.status == "ready" and calls["submitted"] is None
    assert any("usp=pp_url" in line for line in outcome.lines)


def test_process_never_submits_with_problems(stubs):
    calls, state = stubs
    state["judgement"].answers["222"] = []  # 必須の自己PRが書けなかった
    outcome = job_apply.process(mail(text=MAIL_TEXT), SETTINGS, "経歴", "条件", "", False)
    assert outcome.status == "ready" and calls["submitted"] is None
    assert any("自己PR" in line for line in outcome.lines)


def test_process_skips_unmatched_job(stubs):
    calls, state = stubs
    state["judgement"] = job_apply.Judgement("営業代行", False, "", ["SNS運用ではない"], {}, [])
    outcome = job_apply.process(mail(text=MAIL_TEXT), SETTINGS, "経歴", "条件", "", False)
    assert outcome.status == "skipped" and calls["submitted"] is None
    assert any("SNS運用ではない" in line for line in outcome.lines)


def test_process_dry_run_never_submits(stubs):
    calls, _ = stubs
    assert job_apply.process(mail(text=MAIL_TEXT), SETTINGS, "経歴", "条件", "", True).status == "ready"
    assert calls["submitted"] is None


def test_process_without_link():
    outcome = job_apply.process(mail(text="リンクのないお知らせ"), SETTINGS, "経歴", "条件", "", False)
    assert outcome.status == "skipped"


def test_prompt_hides_prefilled_question(form):
    ids = [q["entry_id"] for q in job_apply.form_for_prompt(form)]
    assert "604283207" not in ids and "333" in ids


def test_run_requires_profile_and_criteria():
    with pytest.raises(ValueError):
        job_apply.run({"from_contains": ["x"], "criteria": "c"}, {})
    with pytest.raises(ValueError):
        job_apply.run({"from_contains": ["x"], "profile": "p"}, {})


# ------------------------------------------------------------------ 実行の流れ
class FakeBox:
    def __init__(self, mails, events):
        self.mails, self.events = mails, events

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def search(self, lookback_days):
        return list(self.mails)

    def fetch_message(self, uid):
        return self.mails[uid]

    def set_label(self, uid, on):
        self.events.append(("label" if on else "unlabel", uid))


def test_run_labels_first_and_retries_transient_errors(monkeypatch):
    events, sent = [], []
    mails = {
        b"1": mail(text=MAIL_TEXT).as_bytes(),
        b"2": mail(from_="other@example.com", text=MAIL_TEXT).as_bytes(),
        b"3": mail(text=MAIL_TEXT).as_bytes(),
    }
    monkeypatch.setattr(job_apply, "Mailbox", lambda settings: FakeBox(mails, events))
    results = iter([job_apply.Outcome("applied", "A"), RuntimeError("API down")])

    def fake_process(*args):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(job_apply, "process", fake_process)
    monkeypatch.setattr(job_apply.notify, "dispatch", lambda settings, subject, body: sent.append(body))
    outcomes = job_apply.run({**SETTINGS, "profile": "p", "criteria": "c"}, {}, out=lambda *_: None)
    assert [o.status for o in outcomes] == ["applied"]
    # 差出人違いの2通目は触らない。3通目は失敗したのでラベルを外して次回へ
    assert events == [("label", b"1"), ("label", b"3"), ("unlabel", b"3")]
    assert len(sent) == 1 and "応募しました" in sent[0]
