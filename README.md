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

## What this does and does not protect

This app gives you an **integrity** property. It does not give you a
**confidentiality** property. The distinction matters, so it is spelled out
here rather than left to be inferred.

### The manifest

A "shard" is represented by a colon-delimited manifest string typed into
`agent.input` by whoever launches the run. A real one, from a run in
`demo_output.txt`:

```
shard-c:512rows:cifar10
```

Three fields: the shard label, the row count, the dataset name. That is the
whole schema. There is no manifest generator and no site-side code — an
operator types it. The ledger records its digest and byte length (23 bytes for
the string above, which is exactly its length) but never its content: across
the 40 ledger lines in `demo_output.txt`, zero contain a manifest string.

### What leaves the machine

| | |
| --- | --- |
| SuperLink, AgentApp, `attest_artifact`, the ledger | this machine |
| Model inference | a remote GPU host over **plain HTTP, unauthenticated** |

The prompt — which contains the manifest strings, because the operator put
them there — is sent to the remote model host in cleartext. Replies come back
the same way.

No raw record content leaves the node, but only because **there is no raw
record content anywhere in this app**. It never opens a dataset. That is a
vacuous truth, not an engineered guarantee, and it should not be presented as
one.

The property that *is* engineered: `attest_artifact` executes in this process,
so the digest is computed locally and is never taken on trust from the model.
That is integrity, not privacy.

### Deployed across real hospitals, would raw data leave a trust boundary?

**Yes.** Three reasons, all structural:

1. This bundle declares only an `agentapp` component. It runs in one place —
   the agent task — not on each participant's SuperNode. `clientapp-seconds`
   is `0.0` on every run to date; no site-side code has ever executed.
2. Whatever the AgentApp puts in a prompt goes to whatever
   `FLWR_MODEL_API_ENDPOINT` names. If that endpoint sits outside the trust
   boundary, so does that content.
3. Nothing filters what enters the prompt. There is no redaction, no
   allowlist, no check. If record content reached `agent.input`, it would be
   transmitted.

Getting the confidentiality property would need manifest generation and
attestation to run as a ClientApp on each SuperNode, so only digests return,
and a model endpoint either inside the trust boundary or one that never sees
anything but digests. Neither is true today.

## Sign-off policy

The verdict is computed by `Policy.evaluate` from the attestations the harness
actually executed. The model is not asked and its answer is not consulted:
`run_harness` returns a `HarnessResult` whose `verdict` is the harness's, with
the model's text carried alongside as `model_commentary`.

`agent.required-attestations` (default 2) sets the threshold. A run is refused
when fewer distinct artifacts were attested than required, or when two
artifacts carry the same digest — duplicate content cannot stand in for
independent contributions. Refusals are recorded as `policy.refused`;
satisfied runs as `policy.satisfied`. If the model's own text signed off on a
round the harness refused, that disagreement is recorded as `policy.override`.

Because the verdict does not depend on the model, a provider failure *after*
attestations have been gathered costs only the prose: the harness records
`commentary.unavailable` and decides anyway. A provider failure with nothing
attested raises, since such a run learned nothing.
