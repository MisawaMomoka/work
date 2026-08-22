<#
.SYNOPSIS
    .env ファイルを読み込んで、いまのPowerShellセッションの環境変数にします。

.DESCRIPTION
    bash の `set -a; source .env; set +a` にあたるものです。
    PowerShell には source コマンドが無いため、このスクリプトを使います。

.EXAMPLE
    .\scripts\Load-Env.ps1
    .\scripts\Load-Env.ps1 -Path .env.work
    .\scripts\Load-Env.ps1 -Persist      # 次回以降のセッションでも使えるように保存
#>
[CmdletBinding()]
param(
    [string]$Path = ".env",
    [switch]$Persist
)

if (-not (Test-Path $Path)) {
    Write-Error "$Path が見つかりません。まず 'Copy-Item .env.example .env' を実行してください。"
    exit 1
}

$loaded = @()

foreach ($line in Get-Content $Path -Encoding UTF8) {
    $trimmed = $line.Trim()

    # 空行とコメント行は飛ばす
    if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }

    # 'export NAME=value' の export も許容する
    if ($trimmed -match '^export\s+(.*)$') { $trimmed = $Matches[1].Trim() }

    $separator = $trimmed.IndexOf("=")
    if ($separator -lt 1) { continue }

    $name  = $trimmed.Substring(0, $separator).Trim()
    $value = $trimmed.Substring($separator + 1).Trim()

    # 値を囲むクォートがあれば外す
    if ($value.Length -ge 2 -and
        (($value.StartsWith('"') -and $value.EndsWith('"')) -or
         ($value.StartsWith("'") -and $value.EndsWith("'")))) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    if ($value -eq "") { continue }   # 未記入の項目は設定しない

    Set-Item -Path "env:$name" -Value $value
    if ($Persist) {
        [Environment]::SetEnvironmentVariable($name, $value, "User")
    }
    $loaded += $name
}

if ($loaded.Count -eq 0) {
    Write-Warning "$Path に設定された値がありませんでした。中身を確認してください。"
    exit 1
}

Write-Host "読み込みました: $($loaded -join ', ')" -ForegroundColor Green
if ($Persist) {
    Write-Host "次回以降のPowerShellでも使えるように保存しました。" -ForegroundColor Green
} else {
    Write-Host "有効なのはこのウィンドウだけです。閉じたらもう一度実行してください。"
    Write-Host "毎回入力したくない場合: .\scripts\Load-Env.ps1 -Persist"
}
