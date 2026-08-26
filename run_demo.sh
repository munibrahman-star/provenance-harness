#!/usr/bin/env bash
# Runs the two demo beats. Beat A: two shards, expect a reproducible verdict.
# Beat B: one shard, expect the agent to attest it and decline the verdict.
set -u
MODEL='/models/Qwen3.5-397B-A17B-FP8'
BEAT_B_INPUT="Site C contributed a shard with manifest shard-c:512rows:cifar10. Attest it and say whether the round is reproducible."

echo "### BEAT A - two shards (default input)"
uv run flwr run . local-agent --run-config "agent.model=\"$MODEL\"" --stream 2>&1
echo
echo "### BEAT B - one shard, same question"
uv run flwr run . local-agent \
  --run-config "agent.model=\"$MODEL\" agent.input=\"$BEAT_B_INPUT\"" --stream 2>&1
