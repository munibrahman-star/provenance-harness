#!/usr/bin/env bash
cd "$HOME/flower-hackathon/provenance-harness" || exit 1
L=$(ls -t "$HOME"/.flwr/provenance-ledgers/*.jsonl 2>/dev/null | head -1)
[ -f "$L" ] || { echo "Run ./demo.sh first."; exit 1; }
cp "$L" /tmp/tampered.jsonl
python3 - <<'PY'
import re
p="/tmp/tampered.jsonl"; s=open(p).read()
m=list(re.finditer(r'[0-9a-f]{32,}', s))
t=m[len(m)//2]
old=t.group(0); ch=old[10]
new=old[:10] + ('b' if ch!='b' else 'c') + old[11:]
s=s[:t.start()] + new + s[t.end():]
open(p,'w').write(s)
print(f"\n   changed ONE character inside a hash:\n     {old[:20]}...\n  -> {new[:20]}...\n")
PY
uv run verify /tmp/tampered.jsonl
