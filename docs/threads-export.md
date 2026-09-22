# Threadsの投稿をまとめてスプレッドシートにする

指定した**公開アカウントの投稿を全部拾って、1枚のCSVにする**手順です。
Googleスプレッドシート／Excelにそのまま読み込めます。

```
id, posted_at,        username, type, text,           permalink, ...
3   2026-03-05 21:00  zuck      投稿   写真つきの投稿   https://...
2   2026-02-01 12:30  zuck      返信   返信です         https://...
1   2026-01-01 09:00  zuck      投稿   はじめての投稿   https://...
```

---

## はじめに：できること・できないこと

ここでは **Meta公式の Threads API** だけを使います。画面を自動でめくるスクレイピングは
使いません。Threadsの規約に反するうえ、画面が変わるたびに壊れるからです
（READMEで銀行連携を見送っているのと同じ判断です）。

そのぶん、**公式が決めた制限**をそのまま受けることになります。

| | 自分のアカウント | 他人の公開アカウント |
|---|---|---|
| 必要な権限 | `threads_basic` | `threads_basic` ＋ **`threads_profile_discovery`** |
| Metaのアプリ審査 | 不要 | **必要**（ここが一番の関門） |
| 相手の条件 | — | 公開アカウントで、フォロワーが一定数以上（2025年11月時点で **100人**） |
| 回数の上限 | 緩い | **1,000リクエスト／24時間**（2025年11月時点） |
| いいね数・表示回数 | 取れる | **取れない**（インサイトは自分の投稿だけ） |

> **正直なところ**
> 審査は「アプリで何をするのか」をMetaに説明して通すものです。個人の分析用途でも
> 申請自体はできますが、**通らないこともあります**。まず ④ の `threads-test` で
> 自分のアカウントを対象に動かし、仕組みが動くことを確かめてから審査に進むのが安全です。
> 数字（フォロワー100人・1,000回/24時間）はMetaが予告なく変えます。
> うまくいかないときは [Threads APIのドキュメント](https://developers.facebook.com/docs/threads)で
> 最新の条件を確認してください。

---

## 全体像

```
① Meta for Developers でアプリを作る
        ↓ Threads API を追加
② 自分のThreadsアカウントを接続して、アクセストークンを発行
        ↓
③ THREADS_ACCESS_TOKEN に入れる
        ↓
④ python -m secretary threads-test で疎通を確認（まず自分のアカウントで）
        ↓
⑤ threads_profile_discovery のアプリ審査を申請（他人のアカウントを取る場合）
        ↓
⑥ python -m secretary threads @相手 でCSVに書き出す
```

---

## ① アプリを作る

1. [Meta for Developers](https://developers.facebook.com/) に Facebook / Instagram アカウントでログイン
2. **マイアプリ → アプリを作成**
3. アプリの用途を聞かれたら **その他 / Other** を選ぶ
4. アプリタイプは **ビジネス / Business** を選ぶ
5. アプリ名は自分がわかるものでOK（例: `threads-export`）

作成後、アプリのダッシュボードで **「Threads API」を追加**します。
（製品を追加する一覧に「Threads API」が出てきます）

## ② アクセストークンを発行する

1. アプリのダッシュボード → **Threads API → 設定 / Settings**
2. **Threads Testers** に自分のThreadsアカウントを追加し、
   [Threadsアプリの設定 → ウェブサイトの権限 → 招待](https://www.threads.net/settings/account) から承認する
3. **User Token Generator**（トークン生成）で、追加した自分のアカウントの
   **アクセストークンを生成**する
4. 権限（スコープ）は最低限 `threads_basic` にチェックを入れる

出てきた長い文字列をコピーします。これが `THREADS_ACCESS_TOKEN` です。

> **トークンの寿命**
> 最初に出るのは**短期トークン（1時間）**のことがあります。表示に `short-lived` と
> 書かれていたら、同じ画面にある**長期トークンへの交換**（60日間有効）を使ってください。
> 60日たつと切れるので、定期的に回すなら期限切れ前に取り直します。

## ③ トークンを設定する

**手元で試すとき** — `.env` に書きます。

```bash
cp .env.example .env
# .env を開いて THREADS_ACCESS_TOKEN= の後ろに貼り付ける

# macOS / Linux
set -a; source .env; set +a
```

```powershell
# Windows（PowerShell）
.\scripts\Load-Env.ps1
```

**GitHub Actions で回すとき** — リポジトリの
**Settings → Secrets and variables → Actions → New repository secret** に
`THREADS_ACCESS_TOKEN` という名前で登録します。

> トークンはパスワードと同じです。`config.yaml` に直接書かないでください
> （`config.yaml` はGitHubに上がります。`.env` は `.gitignore` 済みです）。

## ④ 動くか確かめる

まず**自分のアカウント**を対象にします。ここは審査なしで通るはずです。

```bash
python -m secretary threads-test @自分のユーザー名
```

うまくいくとこうなります。

```
アクセストークン : 設定あり（THQVJX…187文字）
APIの入り口      : https://graph.threads.net/v1.0

✓ プロフィール取得OK
  ユーザー名 : @自分のユーザー名
  ユーザーID : 9876543210

✓ 投稿の取得OK（1ページ目で 42件）
  2026-09-20 21:03  [投稿] 今日やったこと
  ...
```

## ⑤ 他人のアカウントを取るための審査（必要な場合だけ）

1. アプリのダッシュボード → **アプリレビュー → 権限と機能**
2. `threads_profile_discovery` を探して **「詳細なアクセスをリクエスト」**
3. 用途の説明と、実際の動作を録画したスクリーンキャストの提出を求められます
   - 「指定した公開アカウントの投稿一覧を取得し、CSVに保存して分析する」など、
     **何を取り、何に使うか**を具体的に書きます
   - スクリーンキャストは `python -m secretary threads @… ` を実行して
     CSVができるまでを撮れば十分なことが多いです
4. 審査結果が出るまで数日かかります

承認されたら、④ を**相手のアカウント名**でもう一度実行して確認します。

## ⑥ CSVに書き出す

```bash
python -m secretary threads @相手のユーザー名
```

```
@example（サンプル太郎） から 1,284件を取得しました。
  うち新規: 1,284件 / CSVの合計: 1,284件

✓ data/threads_posts.csv に 1,284件を書き出しました。
```

よく使うオプション:

| やりたいこと | コマンド |
|---|---|
| 期間を区切る | `--since 2026-01-01 --until 2026-03-31` |
| 返信を除く | `--no-replies` |
| 別のファイルに出す | `--out data/example.csv` |
| 書き込まずに結果だけ見る | `--dry-run` |
| 前回の結果を捨てて取り直す | `--fresh` |
| ページ数の上限を変える | `--max-pages 500`（1ページ100件） |

**2回目以降は差分更新**です。すでにCSVにある投稿はそのまま残り、新しい投稿だけが足されます。
（同じIDの投稿は、新しく取ったほうで上書きします＝本文の編集が反映されます）

毎回コマンドに `@相手` を書くのが面倒なら、`config.yaml` の `threads.username` に
書いておけば省略できます。

## ⑦ スプレッドシートで開く

**Googleスプレッドシート**
「ファイル」→「インポート」→「アップロード」→ `data/threads_posts.csv`
→ 区切り文字は「カンマ」→ インポート

**Excel**
そのままダブルクリックで開けます（文字化けしないようBOM付きで書き出しています）。

---

## CSVの列

| 列 | 中身 |
|---|---|
| `id` | 投稿のID。差分更新の目印に使うので消さないでください |
| `posted_at` | 投稿日時（`config.yaml` の `profile.timezone` に合わせた現地時刻） |
| `username` | 投稿したアカウント名 |
| `type` | `投稿` / `返信` / `引用` / `リポスト` |
| `text` | 本文（改行もそのまま入ります） |
| `media_type` | `TEXT_POST` / `IMAGE` / `VIDEO` / `CAROUSEL_ALBUM` など |
| `permalink` | その投稿のURL |
| `media_url` | 画像・動画のURL（期限付きのことがあります） |
| `link_attachment_url` | 本文に添えられたリンク |
| `topic_tag` | トピックタグ |
| `replied_to_id` | 返信先の投稿ID |
| `root_post_id` | スレッドの起点になった投稿ID |
| `timestamp` | APIが返した元の日時（UTC） |
| `fetched_at` | この行を取得した日時 |

いいね数・表示回数は**他人の投稿では取得できません**（公式APIのインサイトは
自分の投稿だけが対象です）。

---

## 自動で定期実行する

`.github/workflows/threads-export.yml` を用意してあります。

- **手動実行**: GitHubの **Actions** タブ →「Threadsの投稿を書き出す」→ **Run workflow**
  → アカウント名を入れて実行。CSVが更新されてコミットされます。
- **定期実行**: 同じファイルの `schedule:` のコメントを外すと、毎週決まった時間に走ります。
  差分更新なので、回数を重ねても件数は増えすぎません。

`config.yaml` の `threads.username` を埋めておくと、入力欄を空のまま実行できます。

---

## うまくいかないとき

| 出たメッセージ | 意味と直し方 |
|---|---|
| `アクセストークンが未設定です` | `THREADS_ACCESS_TOKEN` が読めていません。`.env` を読み込んだか確認（③） |
| `アクセストークンが無効か期限切れです` | トークンの期限切れ。②で取り直す |
| `threads_profile_discovery のアプリ審査が必要です` | 他人のアカウントを取ろうとしています。⑤へ |
| `@… のプロフィールを取得できませんでした` | 綴り違い／非公開アカウント／フォロワーが少ない（100人未満）のいずれか |
| `リクエストの上限に達しました` | 24時間の上限。翌日に再実行するか、`--since` で範囲を狭める |
| `プロフィールは引けるのに投稿だけ取れない` | 審査待ちの可能性が高いです |
| `このAPIに無い欄を外して取り直します`（警告） | Threads API側の仕様変更。そのまま取れる欄だけで続行します |

**APIの仕様が変わってエンドポイントごと動かなくなった場合**は、`config.yaml` の
`threads.api_base` と `threads.posts_path` を書き換えれば、コードを触らずに追従できます。

```yaml
threads:
  api_base: "https://graph.threads.net/v1.0"
  posts_path: "{user_id}/threads"     # {user_id} は実行時に置き換わります
```
