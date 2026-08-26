#!/usr/bin/env bash
cd "$HOME/flower-hackathon/provenance-harness" || exit 1
clear
printf '\n\033[1;32m▸ RUNNING THE HARNESS\033[0m\n\n'
OUT=$(uv run flwr run . local-agent \
  --run-config 'agent.model="/models/Qwen3.5-397B-A17B-FP8"' --stream 2>&1 | tee /dev/tty)
LEDGER=$(echo "$OUT" | grep -m1 'LEDGER WRITTEN' | sed 's/.*: *//' | tr -d '\r')
[ -f "$LEDGER" ] || LEDGER=$(ls -t "$HOME"/.flwr/provenance-ledgers/*.jsonl 2>/dev/null | head -1)
[ -f "$LEDGER" ] || { printf '\n\033[1;31mNo ledger found.\033[0m\n'; exit 1; }
printf '\n\033[1;32m▸ VERIFYING THE CHAIN\033[0m\n\n'
uv run verify "$LEDGER"
cp "$LEDGER" /tmp/tampered.jsonl
printf '\n\033[1;33m▸ NOW YOU BREAK IT\033[0m\n'
printf '   Change any character, save (Cmd+S), close.\n\n'
open -e /tmp/tampered.jsonl 2>/dev/null
read -r -p "   Press ENTER when saved... "
printf '\n\033[1;32m▸ VERIFYING YOUR EDITED COPY\033[0m\n\n'
uv run verify /tmp/tampered.jsonl
printf '\n   exit code: %s\n\n' "$?"
