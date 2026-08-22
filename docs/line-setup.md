# LINEで毎朝のブリーフィングを受け取る

所要 15〜20分。この手順が終わると、毎朝7時に自分のLINEへ通知が届くようになります。

> **なぜ LINE Notify ではないのか**
> LINE Notify は 2025年3月末で終了しました。いま個人が使える無料の代替は
> **Messaging API**（＝自分専用のBotアカウントを作って、それに喋らせる）です。
> 少し準備が要りますが、一度作れば以後さわる必要はありません。

---

## 全体像

```
① LINE公式アカウントを作る           …… これが「Bot」の実体
        ↓ Messaging API を有効化
② チャネルアクセストークン（長期）を発行  …… これがパスワード代わり
        ↓
③ 自分のLINEでそのBotを友だち追加       …… ここに毎朝届く
```

---

## ① LINE公式アカウントを作り、Messaging API を有効にする

Messaging APIチャネルは単体では作れません。**まずLINE公式アカウントを作り、そこで
Messaging APIの利用を有効にすると、対応するチャネルが自動で用意される**という順番です。

1. [LINE Developers コンソール](https://developers.line.biz/console/) に、ふだん使っている
   LINEアカウントでログインする
2. 初回は開発者名とメールアドレスの登録を求められるので入力する
3. **新規プロバイダー作成** → 名前は自分の名前やハンドルネームでOK
   （プロバイダー＝アプリの提供者。個人利用ならただの入れ物です）
4. そのプロバイダーの中で **新規チャネル作成** → **Messaging API** を選ぶ
5. LINE公式アカウントの作成フォームに進むので入力する
   - アカウント名: `〇〇の秘書` など（**自分のLINEに表示される名前**です）
   - 業種などは個人利用なら適当で構いません
6. 作成が終わると、コンソールに Messaging API チャネルが並びます

> 途中で [LINE Official Account Manager](https://manager.line.biz/) 側に飛ばされることがあります。
> その場合は **設定 → Messaging API → 利用する** を有効にすれば同じ状態になります。

## ② チャネルアクセストークン（長期）を発行する

1. 作ったチャネルを開く → **Messaging API設定** タブ
2. 一番下までスクロール → **チャネルアクセストークン（長期）** の **発行** ボタン
3. 表示された長い文字列をコピーする

これが `LINE_CHANNEL_ACCESS_TOKEN` の値です。

> ⚠️ **このトークンを知っている人は、あなたのBotとして自由にメッセージを送れます。**
> スクリーンショットに写さない、リポジトリにコミットしない、チャットに貼らない。
> 万一漏れたら同じ画面で **再発行** すれば古いものは無効になります。

## ③ 自分のLINEで友だち追加する

同じ **Messaging API設定** タブの上のほうに **QRコード** があります。
スマホのLINEで読み取って友だち追加してください。**ここが毎朝の通知の届き先**になります。

## ④ 自動応答をオフにする（おすすめ）

初期状態のBotは、こちらが何か送ると「このアカウントでは個別のご返信ができません」等を
自動で返します。うるさいので切っておきます。

[LINE Official Account Manager](https://manager.line.biz/) → 対象アカウント →
**設定 → 応答設定** で、

- **応答メッセージ** … オフ
- **あいさつメッセージ** … オフ（お好みで）
- **Webhook** … オフのままでOK（このツールは受信しないため）

---

## ⑤ 手元で動作確認する

```powershell
# Windows (PowerShell)
$env:LINE_CHANNEL_ACCESS_TOKEN = "②でコピーした文字列"
# LINE_USER_ID は空のままでOK（理由は後述）

# 次回以降も使うなら
[Environment]::SetEnvironmentVariable("LINE_CHANNEL_ACCESS_TOKEN", "②の文字列", "User")
```

```bash
# macOS / Linux
export LINE_CHANNEL_ACCESS_TOKEN="②でコピーした文字列"
```

設定する値が増えてきたら、`.env` にまとめて書いて読み込めます。

```powershell
Copy-Item .env.example .env   # 値を書き込んで保存
.\scripts\Load-Env.ps1        # PowerShell（-Persist で次回以降も）
```
```bash
cp .env.example .env
source scripts/load-env.sh    # macOS / Linux
```

`config.yaml` の LINE をオンにします。

```yaml
notify:
  console:
    enabled: true
  line:
    enabled: true          # ← ここを true に
    channel_access_token: "${LINE_CHANNEL_ACCESS_TOKEN}"
    to: "${LINE_USER_ID}"
```

まず**送らずに**設定だけ確認します。

```bash
python -m secretary line-test
```

```
✓ トークン有効: ももかの秘書 (@123abcde)
  送信先     : 友だち全員（broadcast）
  今月の送信 : 3 / 200 通

実際に1通送るには: python -m secretary line-test --send
```

ここまで出れば設定は正しいです。次に本当に1通送ります。

```bash
python -m secretary line-test --send      # テストメッセージ
python -m secretary brief                 # 本物のブリーフィング
```

LINEに届いたら成功です。

## ⑥ GitHub Secrets に登録して自動化する

リポジトリの **Settings → Secrets and variables → Actions → New repository secret**:

| 名前 | 値 |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | ②でコピーしたトークン |
| `CALENDAR_ICS_URL` | Googleカレンダーの非公開iCal URL |
| `ANTHROPIC_API_KEY` | （任意）AIで文章を整えるなら |

`config.yaml` の `notify.line.enabled: true` をコミットするのを忘れずに。
（トークン本体はコミットしません。`${LINE_CHANNEL_ACCESS_TOKEN}` のままでOK）

登録できたら **Actions → 毎朝のブリーフィング → Run workflow** で手動テスト。
届けば完了です。あとは毎朝7時（JST）に自動で届きます。

---

## 送信先について：`LINE_USER_ID` は空でいい

このBotは**あなた専用**なので、友だちは実質あなた1人です。
`to` が空だと **broadcast（友だち全員に送信）** になり、結果としてあなただけに届きます。
ユーザーIDを調べる手間が省けるので、**空のままを推奨**します。

家族や同僚も同じBotを友だち追加していて、自分だけに送りたい場合のみ
`LINE_USER_ID` に自分のユーザーID（`U` で始まる33文字）を設定してください。
LINE Developers コンソールのチャネル基本情報に、自分のユーザーIDが表示されています。

## 料金

無料の **コミュニケーションプラン** で **月200通** まで送れます。
1日1通なら月31通なので、十分に収まります。

- broadcast は「友だちの人数ぶん」消費します（自分1人なら1通）
- 長文はメッセージが分割されることがあり、その分カウントされます
- 残数は `python -m secretary line-test` でいつでも確認できます

参考: [LINE公式アカウント 料金プラン](https://www.lycbiz.com/jp/service/line-official-account/plan/)

> 2026年10月1日から追加メッセージの料金体系が改定される予定です。
> 無料枠の範囲で使うぶんには影響ありませんが、通数を増やすときは最新の料金を確認してください。

---

## うまくいかないとき

`python -m secretary line-test` の出力で切り分けられます。

| 症状 | 原因と対処 |
|---|---|
| `LINE API 401` | トークンが違う / 期限切れ。**Messaging API設定**タブで再発行してコピーし直す。前後の空白や改行が混ざっていないかも確認 |
| `LINE API 403` | LINEログインチャネルなど、Messaging API ではないチャネルのトークンを使っている。チャネルの種類を確認 |
| `LINE API 429` | 今月の無料通数を使い切っている。翌月まで待つか、プランを見直す |
| `✓ トークン有効` は出るが届かない | Botを友だち追加していない（③）。または追加後にブロックしている |
| 余計な自動返信が来る | ④の応答メッセージがオンのまま |
| GitHub Actionsだけ失敗する | Secret名のタイプミス。`LINE_CHANNEL_ACCESS_TOKEN` と完全一致か確認 |
| Actionsは成功なのに届かない | `config.yaml` の `notify.line.enabled` が `false` のままコミットされている |

それでも切り分かないときは、Actionsのログを見てください。
`brief` は失敗したチャネル名と理由をログに出します。
（1つのチャネルが失敗しても他のチャネルへの送信は続行されます）
