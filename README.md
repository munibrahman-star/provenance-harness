# provenance-harness

A Flower **AgentApp** that keeps a tamper-evident provenance ledger of its own run.

Every step — each model request, each model response, each tool call — is appended to a
hash chain: entry *n*'s `chain` is `sha256(chain[n-1] || canonical_json(entry_n))`.
Editing any earlier entry invalidates every hash after it. The ledger is printed at the
end of the run, emitted as `provenance.ledger.entry` run events, and stored in
`context.state["provenance"]`.

## Layout

| file | role |
| --- | --- |
| `provenance_harness/harness.py` | the agent loop, ledger, tools — no Flower imports |
| `provenance_harness/agent_app.py` | thin `AgentApp` wrapper around the loop |
| `local_probe.py` | drives the same loop straight at an Open Responses endpoint, no SuperLink |

## Run

Point the SuperLink at an Open Responses-compatible endpoint and start it:

```bash
export FLWR_MODEL_API_ENDPOINT='http://<host>:8001/v1/responses'
export FLWR_MODEL_API_TIMEOUT=90   # fail a stalled request fast enough to retry
unset FLWR_MODEL_API_KEY           # only needed for api.flower.ai
uv run flower-superlink --insecure
```

`~/.flwr/config.toml` needs the matching SuperLink entry:

```toml
[superlink.local-agent]
address = "127.0.0.1:9093"
insecure = true
```

Then, from this directory:

```bash
uv run flwr run . local-agent \
  --run-config 'agent.model="/models/Qwen3.5-397B-A17B-FP8"' --stream
```

Run config keys: `agent.model`, `agent.input`, `agent.tools`, `agent.max-turns`.

To iterate on the loop without the Flower stack in the way:

```bash
python3 local_probe.py
```

## Note on tool calls

vLLM only emits Open Responses `function_call` items when it is started with
`--enable-auto-tool-choice` and a tool-call parser. Without one, Qwen returns its raw
`<tool_call>` block as assistant text. The harness prefers native `function_call` items
and falls back to parsing that text, recording a `provider.unparsed_tool_calls` ledger
entry whenever it has to. Both the Qwen XML and Hermes JSON dialects are handled.

## Provider failures

A failing model task does not raise — it replies with an ordinary Open
Responses payload carrying an `error` object. The harness checks for that
explicitly (`provider_error`), retries up to `MAX_ATTEMPTS_PER_TURN` times with
backoff, and records every attempt in the ledger as `model.error` /
`model.recovered`. If the attempts are exhausted the run raises and is recorded
as `run.failed` rather than completing with an empty answer.

Keep `FLWR_MODEL_API_TIMEOUT` well below 300s (the AgentApp's reply timeout) so
a stalled request surfaces as a retryable error instead of taking the whole
task down with it.
