#!/usr/bin/env bash
# .env を読み込んで、いまのシェルの環境変数にします。
#   使い方: source scripts/load-env.sh  [ファイル名]
# （PowerShell では scripts/Load-Env.ps1 を使ってください）

_envfile="${1:-.env}"

if [ ! -f "$_envfile" ]; then
    echo "$_envfile が見つかりません。まず 'cp .env.example .env' を実行してください。" >&2
    return 1 2>/dev/null || exit 1
fi

set -a
# shellcheck disable=SC1090
. "$_envfile"
set +a

echo "読み込みました: $_envfile"
unset _envfile
