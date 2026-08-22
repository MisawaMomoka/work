# カレンダーをつなぐ

所要 5〜10分。Googleカレンダーの「非公開URL」を1つコピーして貼るだけです。
**OAuthの設定もAPIキーも要りません。**

---

## ① 非公開URL（iCal形式）をコピーする

1. パソコンのブラウザで [Googleカレンダー](https://calendar.google.com/) を開く
2. 画面左の「マイカレンダー」で、使いたいカレンダーにマウスを乗せる
3. 右に出る **⋮**（縦三点）→ **設定と共有**
4. 左メニューまたはページを下にスクロールして **カレンダーの統合**
5. **カレンダーの非公開URL（iCal形式）** の欄にあるコピーボタンを押す

コピーしたURLはこういう形です。

```
https://calendar.google.com/calendar/ical/xxxxxxxx%40group.calendar.google.com/private-yyyyyyyy/basic.ics
```

> **⚠️ 3つのURLが並んでいるので間違えないでください**
>
> | 欄の名前 | 使う？ |
> |---|---|
> | 公開URL / 埋め込みコード | ✗ HTMLページなので読めません |
> | このカレンダーの公開URL（iCal形式） | △ カレンダーを一般公開している場合のみ |
> | **カレンダーの非公開URL（iCal形式）** | **✓ これを使います** |
>
> 見分け方: **末尾が `/basic.ics` で、途中に `private-` が入っていれば正解**です。

> **このURLは実質パスワードです。** 知っている人はあなたの予定を全部読めます。
> スクショに写さない、リポジトリにコミットしない、チャットに貼らない。
> 万一漏れたら、同じ画面の **リセット** ボタンで無効化できます。

## ② 環境変数に設定する

### Windows（PowerShell）

**このウィンドウだけで有効にする**（まず試すならこれ）:

```powershell
$env:CALENDAR_ICS_URL = "ここにコピーしたURLを貼る"
```

**次回以降も使えるように保存する**:

```powershell
[Environment]::SetEnvironmentVariable("CALENDAR_ICS_URL", "ここにURLを貼る", "User")
```

> 保存した場合、**いまのウィンドウにはまだ反映されません。**
> PowerShellを一度閉じて開き直すか、上の `$env:` の行も実行してください。

**設定する変数が増えてきたら**、`.env` にまとめて書いて読み込めます。

```powershell
Copy-Item .env.example .env
notepad .env                      # 値を書き込んで保存

.\scripts\Load-Env.ps1            # このウィンドウで有効化
.\scripts\Load-Env.ps1 -Persist   # 次回以降も有効化
```

> `.ps1 を読み込めません` と出る場合は、いまのウィンドウだけ許可します:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

### macOS / Linux

```bash
export CALENDAR_ICS_URL="ここにURLを貼る"

# または .env にまとめて書く
cp .env.example .env && $EDITOR .env
source scripts/load-env.sh
```

## ③ つながったか確認する

```powershell
python -m secretary calendar-test
```

```
カレンダー1: https://calendar.google.com/calendar/ical/…/basic.ics
  ✓ 取得OK  カレンダー名: 仕事
    登録されている予定: 47件（繰り返しは1件として数えます）

  今日（8月22日）の予定: 2件
    ・終日  健康診断
    ・13:00–14:30  A社 打ち合わせ  @渋谷オフィス

  この先7日間:
    8/23(日)  — 0件
    8/24(月)  ■ 1件
    8/25(火)  ■■ 2件
    ...

カレンダー接続は正常です。次: python -m secretary brief --dry-run --no-ai
```

**自分の予定が実際に表示されていれば成功です。** 続けてブリーフィングを見てみましょう。

```powershell
python -m secretary brief --dry-run --no-ai
```

---

## 複数のカレンダーを見たいとき

仕事用とプライベート用を両方見たい場合は、`config.yaml` に足します。

```yaml
calendar:
  ics_urls:
    - "${CALENDAR_ICS_URL}"
    - "${CALENDAR_ICS_URL_PRIVATE}"    # 環境変数を増やす
```

環境変数もその名前で追加してください。予定は時刻順にまとめて並びます。
1つが読めなくても、残りのカレンダーとブリーフィング自体は出ます。

## Outlook / iCloud の場合

同じ仕組みで動きます。ICS形式のURLを取得して同じ環境変数に入れてください。

- **Outlook (outlook.com)**: 設定 → カレンダー → 共有 → 公開 → ICSリンクを取得
- **iCloud**: カレンダーApp → カレンダー名の横の共有アイコン → 「公開カレンダー」→ URLをコピー
  （`webcal://` で始まりますが、そのまま貼って大丈夫です。自動で変換します）

## 空き時間の判定を変える

`config.yaml` で調整できます。

```yaml
profile:
  work_hours: ["09:00", "18:00"]   # この時間帯のなかで空きを探す
calendar:
  free_slot_min_minutes: 45        # 45分以上あいていたら「空き」と呼ぶ
```

終日の予定（有給など）は、空き時間の計算には影響しません。

---

## うまくいかないとき

`python -m secretary calendar-test` の出力で切り分けられます。

| 出力 | 原因と対処 |
|---|---|
| `カレンダーのURLが設定されていません` | 環境変数が入っていません。PowerShellを開き直したなら `$env:` の設定が消えています（②の「保存する」を使ってください） |
| `カレンダーとして読めませんでした` | 公開URLや設定ページのURLを貼っています。**末尾が `/basic.ics`** のものを使ってください |
| `HTTP 404 が返りました` | URLが古いか間違い。カレンダーの設定画面でコピーし直してください |
| `接続できませんでした` | ネットワークかプロキシの問題。ブラウザでそのURLを開いて .ics がダウンロードされるか確認 |
| `✓ 取得OK` だが今日の予定が0件 | 本当に予定が無いだけかもしれません。「この先7日間」に予定が出ていればつながっています |
| 別のカレンダーの予定が出る | 複数カレンダーがある場合、コピー元を間違えています。目的のカレンダーの ⋮ から取り直してください |
| 繰り返し予定が出てこない | 例外日を個別変更した複雑な繰り返しは未対応です。単純な繰り返し（毎日/毎週/毎月）は動きます |

## GitHub Actions で動かすとき

手元で確認できたら、同じURLを GitHub Secrets にも登録します。

**Settings → Secrets and variables → Actions → New repository secret**

| 名前 | 値 |
|---|---|
| `CALENDAR_ICS_URL` | ①でコピーしたURL |

登録後、**Actions → 毎朝のブリーフィング → Run workflow** で動作確認してください。
