# AI秘書 — 毎朝、今日やることをまとめて届ける

仕事のスケジュールとお金の管理を自動化するための、小さな仕組みです。
**毎朝きまった時間に、その日の「予定・タスク・お金」を1通にまとめて送ります。**

```
☀️ 2026年8月25日（火）のブリーフィング

📅 予定
・09:30–09:45　朝会
・13:00–14:30　A社 打ち合わせ　@渋谷オフィス
・空き: 09:45–13:00（195分） / 14:30–18:00（210分）

✅ タスク
・[期限切れ 2日] 経費精算
★請求書を送る
・メールと Slack の未読を片づける
・まもなく: 健康診断の予約(8/27)

💰 お金
・2026年8月: 71,500円 / 120,000円（残り 48,500円）
・残り7日 → 1日あたり 6,928円 まで
・引き落とし 8/27: 家賃 82,000円
・⚠️ 「趣味」がカテゴリ予算を超えています
```

---

## 考え方（なぜこの構成か）

やりたいことは3つです。**予定を知る・やることを知る・お金の残りを知る**。
それぞれ「一番手間のかからないやり方」を選んでいます。

| やりたいこと | このリポジトリでのやり方 | なぜ |
|---|---|---|
| 予定を取ってくる | **カレンダーの iCal(ICS) URL を読む** | Google/Outlook/iCloud 共通。OAuth設定もAPIキーも不要で、URLを1つ貼るだけ |
| やることを管理する | **`data/tasks.yaml` に書く** | 新しいアプリを増やさない。「毎日」「平日」「毎月15日」の繰り返しに対応 |
| お金を管理する | **固定費は設定に、変動費は `data/expenses.csv` に1行ずつ** | 銀行の自動連携は個人だと現実的に無理。**代わりに「今月あといくら使えるか」を毎朝出す**ほうが効く |
| 文章にまとめる | **Claude API** | データを渡すと秘書らしい文章にしてくれる。キーが無くても素のテンプレートで動く |
| 毎朝実行する | **GitHub Actions の cron** | サーバー不要・無料枠内。PCを開いていなくても動く |
| 届ける | **LINE / Slack / Discord / メール** | 使っているものを選ぶ。複数同時もOK |

> **銀行・クレカの自動連携について**
> 個人が使える公式APIはほぼ無く、スクレイピングは規約違反かつすぐ壊れます。
> ここでは連携せず、**「予算 − 使った分 = 今日使っていい額」を毎朝突きつける**方式にしました。
> 入力の手間は、スマホから1タップで足せる仕組み（後述の「支出を記録」ワークフロー）で吸収します。
> すでに Zaim / マネーフォワード を使っているなら、そこからCSVを書き出して
> `data/expenses.csv` に流し込めば同じように動きます。

---

## セットアップ（30分くらい）

### 0. 手元に持ってきて、動かしてみる（設定なしでOK）

**まずリポジトリをダウンロードし、そのフォルダに移動してから**実行します。
ホームフォルダのまま実行すると `No such file or directory: 'requirements.txt'` になります。

<details open>
<summary><b>Windows（PowerShell）</b></summary>

```powershell
cd ~
git clone https://github.com/MisawaMomoka/work.git ai-secretary
cd ai-secretary
git checkout claude/ai-secretary-work-automation-3kklmy

python -m pip install -r requirements.txt
python -m secretary brief --dry-run --no-ai
```

`git` が入っていない場合は、GitHubのページで **Code → Download ZIP** して展開し、
展開先フォルダで `cd` してから 2つのコマンドを実行してください。
</details>

<details>
<summary><b>macOS / Linux</b></summary>

```bash
git clone https://github.com/MisawaMomoka/work.git ai-secretary
cd ai-secretary
git checkout claude/ai-secretary-work-automation-3kklmy

python3 -m pip install -r requirements.txt
python3 -m secretary brief --dry-run --no-ai
```
</details>

うまくいくと、サンプルのタスクでこう出ます。まずここを通してください。

```
☀️ 2026年8月22日（土）のブリーフィング

📅 予定
・予定なし
...
```

> **以降のコマンドは、すべてこのフォルダの中で実行します。**
> 別のフォルダに移動してしまったら `cd ~/ai-secretary` で戻ってください。
> いまどこにいるか分からなくなったら、PowerShellなら `pwd`、
> ファイルが揃っているかは `ls` で確認できます（`requirements.txt` が見えていればOK）。

### 1. カレンダーをつなぐ

Googleカレンダーの場合:

1. カレンダー左のリストで対象カレンダーの「⋮」→ **設定と共有**
2. 一番下の **カレンダーの統合** → **カレンダーの非公開URL（iCal形式）** をコピー
3. 環境変数 `CALENDAR_ICS_URL` に設定

> このURLを知っている人は予定を全部見られます。**必ず Secrets / `.env` に入れて、コミットしないでください。**
> 複数のカレンダーを見たいときは `config.yaml` の `calendar.ics_urls` に足していけます。

```bash
cp .env.example .env      # 値を書き込む
set -a; source .env; set +a
python -m secretary doctor    # つながったか確認
```

### 2. 通知先を決める

`config.yaml` の `notify` で、使うものを `enabled: true` にします（複数可）。

<details>
<summary><b>LINE</b>（スマホで受け取りたい人向け・おすすめ）</summary>

LINE Notify は 2025年3月末に終了したので、**Messaging API** を使います。

1. [LINE Developers](https://developers.line.biz/console/) でLINE公式アカウントを作り、Messaging API を有効化
2. 「Messaging API設定」タブ → **チャネルアクセストークン（長期）** を発行 → `LINE_CHANNEL_ACCESS_TOKEN`
3. 同じタブのQRコードで、自分のLINEからそのBotを友だち追加
4. `LINE_USER_ID` は**空のままでOK**（友だち全員に送る broadcast になり、自分1人なら実質それで十分）

設定できたか確認するコマンドがあります。

```bash
python -m secretary line-test          # トークン・送信先・今月の残り通数を確認
python -m secretary line-test --send   # 実際に1通送ってみる
```

無料のコミュニケーションプランで月200通まで。1日1通なら余裕です。

**→ 画面の場所や詰まりどころまで含めた詳細手順は [docs/line-setup.md](docs/line-setup.md)**
</details>

<details>
<summary><b>Slack</b></summary>

1. https://api.slack.com/apps → Create New App → From scratch
2. **Incoming Webhooks** を On → Add New Webhook to Workspace → 送信先チャンネルを選択
3. 出てきたURLを `SLACK_WEBHOOK_URL` へ
</details>

<details>
<summary><b>Discord</b></summary>

チャンネル設定 → 連携サービス → ウェブフック → 新しいウェブフック → URLをコピー → `DISCORD_WEBHOOK_URL`
</details>

<details>
<summary><b>メール（Gmail）</b></summary>

1. Googleアカウントで2段階認証を有効化
2. [アプリパスワード](https://myaccount.google.com/apppasswords) を発行
3. `SMTP_USERNAME`（Gmailアドレス）、`SMTP_PASSWORD`（アプリパスワード）、`NOTIFY_EMAIL`（宛先）を設定
</details>

### 3. お金の設定を自分用に直す

`config.yaml` の `money` を書き換えます。ここだけは自分の数字にしてください。

```yaml
money:
  monthly_budget: 120000        # 1か月に使っていい変動費の合計
  category_budgets:
    食費: 45000
    交際費: 20000
  fixed_costs:                  # 毎月きまって落ちるもの
    - {name: "家賃",   amount: 82000, day: 27}
    - {name: "電気",   amount:  7000, day: 5}
  remind_days_before: 3         # 引き落とし3日前から通知
```

支出はこう足します。

```bash
python -m secretary add 食費 1200 ランチ
python -m secretary add 2026-08-18 交通費 480
python -m secretary money        # いまの状況を確認
```

### 4. AIに文章を書かせる（任意）

[Anthropic Console](https://console.anthropic.com/) でAPIキーを作り、`ANTHROPIC_API_KEY` に設定します。
**未設定でも動きます**（そのときは素のテンプレート出力になります）。
1日1通なら費用は月あたり数十円程度です。

### 5. 毎朝の自動実行をONにする

GitHubリポジトリの **Settings → Secrets and variables → Actions** で、使う値を登録します。

| Secret名 | 用途 |
|---|---|
| `CALENDAR_ICS_URL` | カレンダー |
| `ANTHROPIC_API_KEY` | AI整形（任意） |
| `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID` | LINE |
| `SLACK_WEBHOOK_URL` | Slack |
| `DISCORD_WEBHOOK_URL` | Discord |
| `SMTP_USERNAME` / `SMTP_PASSWORD` / `NOTIFY_EMAIL` | メール |

登録したら **Actions タブ → 「毎朝のブリーフィング」→ Run workflow** で手動テスト。
うまくいけば、あとは毎朝7時（JST）に自動で届きます。

時刻を変えるときは `.github/workflows/morning-brief.yml` の cron を編集してください（**UTC指定**です）。

```yaml
- cron: "0 22 * * *"     # 22:00 UTC = 翌 07:00 JST（毎日）
# - cron: "30 21 * * 0-4"  # 06:30 JST（月〜金だけ）
```

> GitHubのcronは混雑時に数分〜十数分遅れることがあります。分単位の正確さが要るなら
> 予定時刻を少し早めに設定しておくのが実用的です。

---

## 普段の使い方

```bash
python -m secretary brief                 # ブリーフィングを作って送る
python -m secretary brief --dry-run       # 送らずに画面で確認
python -m secretary brief --date 2026-09-01   # 別の日で試す

python -m secretary add 食費 1200 ランチ   # 支出を記録
python -m secretary money                 # 家計の状況

python -m secretary task list
python -m secretary task add "請求書を送る" --due 2026-09-01 --priority high
python -m secretary task done 3

python -m secretary line-test             # LINEの設定確認（--send でテスト送信）
python -m secretary doctor                # 設定の健康診断
```

**外出先で支出を記録したいとき**は、GitHubアプリ / ブラウザから
**Actions → 「支出を記録」→ Run workflow** でカテゴリと金額を入れるだけ。
`data/expenses.csv` に自動でコミットされます。

### タスクの書き方（`data/tasks.yaml`）

```yaml
- title: 請求書を送る
  due: 2026-09-01
  priority: high        # high / normal / low
  project: 経理

- title: 日報を書く
  repeat: weekdays      # daily / weekdays / weekends / weekly:mon / monthly:15

- 牛乳を買う            # 文字列だけでもOK（期限なし）
```

`done: true` を付けると通知から消えます。期限を過ぎたものは「期限切れ ◯日」として先頭に出ます。

---

## 中身

```
secretary/
  __main__.py          コマンドライン
  config.py            設定読み込み（${ENV} を環境変数に展開）
  pipeline.py          各ソースを集めて1つのブリーフィングにする
  brief.py             文章化（テンプレート + Claude API）
  models.py            データ構造
  sources/
    calendar_ics.py    ICS取得・繰り返し予定の展開・空き時間の計算
    tasks.py           tasks.yaml の読み書きと今日ぶんの絞り込み
    money.py           支出集計・予算残り・引き落とし予定
  notify/
    line.py  slack.py  discord.py  email.py  console.py
docs/
  line-setup.md        LINEで受け取るまでの詳細手順
```

設計上こだわった点:

- **朝の通知が止まらないこと。** カレンダーが落ちても、APIキーが無くても、
  支出CSVに壊れた行があっても、通知そのものは必ず出ます。
  失敗したものは本文の末尾に `※` で理由が出ます。
- **AIは「あると良い」レイヤー。** 数字の計算はすべてPython側でやっていて、
  AIは体裁を整えるだけです。金額や時刻をAIが作り出すことはありません。
- **秘密情報はリポジトリに入れない。** `config.yaml` には構造だけを書き、
  値は `${ENV_NAME}` 経由で環境変数から読みます。

テスト:

```bash
pip install pytest && python -m pytest -q
```

---

## つまずいたら

| 症状 | 見るところ |
|---|---|
| `No such file or directory: 'requirements.txt'` | リポジトリのフォルダの外にいます。`cd ai-secretary` してから実行 |
| `No module named secretary` | 同上。`ls` で `secretary` フォルダが見えるところに移動する |
| `ZoneInfoNotFoundError` / タイムゾーンのエラー | `python -m pip install tzdata`（Windowsのみ必要） |
| 予定が空のまま | `python -m secretary doctor`。ICS URLが「非公開URL(iCal形式)」か確認 |
| 通知が来ない | `config.yaml` の `notify.*.enabled` が `true` か。GitHub Secretsの名前が合っているか |
| LINEに届かない | `python -m secretary line-test` で切り分け。詳細は [docs/line-setup.md](docs/line-setup.md#うまくいかないとき) |
| 時間がずれる | cronは**UTC**。JSTから9時間引いた値を書く |
| 繰り返し予定が出ない | 一部の複雑なRRULE（例外日の個別変更など）は未対応。単純な繰り返しは動きます |
| AIが使われていない | `ANTHROPIC_API_KEY` を確認。未設定だと自動でテンプレート出力になります |

## これから足すなら

- 前月比・グラフ付きの月次レポート（毎月1日に送る）
- 天気と、外出予定がある日の「傘いる/いらない」
- 予定の前日リマインド（準備が要る会議だけ）
- Zaim API 連携（公式APIがあるので、`sources/money.py` に読み込み口を足す形で入れられます）
