#!/bin/bash
# Double-click this file in Finder (Mac) to refresh the Rex Concepts report.
set -euo pipefail
cd "$(dirname "$0")"

echo "=== Rex Concepts report update ==="
echo ""
echo "1) Chrome must be open and logged in to Looker (bolt.cloud.looker.com)."
echo "2) Press Enter here when Looker works in Chrome."
read -r _

ENV_FILE="$HOME/.rex_concepts_env"
if [[ ! -f "$ENV_FILE" && -f "$HOME/.hesburger_ro_env" ]]; then
  ENV_FILE="$HOME/.hesburger_ro_env"
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo ""
  echo "ERROR: Missing $HOME/.rex_concepts_env (GITHUB_TOKEN)."
  echo "The Hesburger file ~/.hesburger_ro_env is also accepted."
  echo "Press Enter to close."
  read -r _
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

if [[ -f "$HOME/scripts/rex_concepts_update.py" ]]; then
  PY="$HOME/scripts/rex_concepts_update.py"
elif [[ -f "$(dirname "$0")/rex_concepts_update.py" ]]; then
  PY="$(dirname "$0")/rex_concepts_update.py"
else
  echo ""
  echo "ERROR: rex_concepts_update.py not found in ~/scripts/ or this folder."
  echo "Download from: https://github.com/elenapopa-bot/RexConcepts/tree/main/scripts"
  echo "Press Enter to close."
  read -r _
  exit 1
fi

echo ""
echo "Running: python3 $PY"
echo ""
python3 "$PY" || {
  echo ""
  echo "FAILED — see ~/scripts/rex_concepts.log"
  echo "Press Enter to close."
  read -r _
  exit 1
}

echo ""
echo "Done. Report: https://github.com/elenapopa-bot/RexConcepts/blob/main/rex_concepts_report.html"
echo "Press Enter to close."
read -r _
