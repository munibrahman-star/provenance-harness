# Where Flower is used in Provenance Harness

**Munib Rahman · SEEVAD · Track 1: SuperGrid**
`@munibrahman/provenance-harness` · github.com/munibrahman-star/provenance-harness

Flower is not a dependency in this project — it is the runtime the whole thing lives inside.
Below is every point of contact, including the two places where *not* using Flower was the
design decision.

---

## 1. It is a Flower AgentApp

`pyproject.toml` declares an agentapp bundle:

```toml
[tool.flwr.app.components]
agentapp = "provenance_harness.agent_app:app"
```

There is no `serverapp` and no `clientapp`. `provenance_harness/agent_app.py` is a thin
`AgentApp` wrapper — `@app.main()` receiving `AgentSession` and `Context`. The agent loop
itself (`harness.py`) has no Flower imports at all, so the framework boundary is explicit
and testable.

Launched with:

```bash
flwr run . local-agent --run-config 'agent.model="/models/Qwen3.5-397B-A17B-FP8"' --stream
```

## 2. Flower orchestrates the run

SuperLink assigns the run ID, starts the `flwr-agentapp` process, creates an isolated
runtime environment at `~/.flwr/runtime-envs/<run-id>`, and installs dependencies via
`uv sync`. All visible in the run log. The run ID is not cosmetic — it names the ledger
file, so every artifact is traceable back to a specific Flower run.

## 3. Model calls go through Flower's runtime bridge

The AgentApp never contacts the model host directly. SuperLink is configured with
`FLWR_MODEL_API_ENDPOINT` and routes the Open Responses request on the app's behalf.

Consequence: switching between Qwen3.5 397B, Kimi-K2.7-Code, GLM-5.2 and MiniMax-M3 is an
environment variable, not a code change. The same app also runs against a local Ollama
endpoint with no account and no network.

## 4. The ledger IS Flower's event stream — the deepest integration

Every ledger entry is emitted through:

```python
agent.events.emit({"type": "provenance.ledger.entry", ...})
```

This is not a side log file written in parallel. The audit trail is carried on Flower's own
structured run-event channel, so it surfaces in `flwr run --stream` and to any run-event
subscriber. The provenance record and the framework's observability are the same object.

## 5. Flower's Context carries the ledger

The ledger is stored in `context.state["provenance"]`, so it lives in the Flower-managed
run state rather than in module-level globals.

## 6. Flower's failure semantics shaped the design

A failing model task in Flower does not raise — it returns an ordinary Open Responses
payload carrying an `error` object. A naive harness would treat that as a valid empty
answer and sign off on nothing.

This harness checks for it explicitly (`provider_error`), retries with backoff up to
`MAX_ATTEMPTS_PER_TURN`, and records every attempt as `model.error` / `model.recovered`.
If attempts are exhausted the run raises and is recorded as `run.failed` rather than
completing silently.

`FLWR_MODEL_API_TIMEOUT` is deliberately set to 90s — well below the AgentApp's 300s reply
timeout — so a stalled request surfaces as a retryable error instead of taking the whole
task down. This was observed live during testing: a provider stall at 96s, recovered on
attempt 2, run completed, both events on the ledger.

## 7. Run configuration is Flower's

`agent.model`, `agent.input`, `agent.tools`, `agent.max-turns` — all supplied through
`--run-config` and read from `context.run_config`. Nothing is hard-coded.

## 8. Provider quirks handled at the Flower boundary

vLLM only emits Open Responses `function_call` items when started with
`--enable-auto-tool-choice` and a tool-call parser. Without one, Qwen returns its raw
`<tool_call>` block as assistant text. The harness prefers native `function_call` items and
falls back to parsing that text — recording a `provider.unparsed_tool_calls` ledger entry
whenever it has to. Both the Qwen XML and Hermes JSON dialects are handled. The workaround
is on the record, not hidden.

## 9. SuperLink connection management

`~/.flwr/config.toml` holds both a `local-agent` connection (insecure, 127.0.0.1:9093) and
the `supergrid` connection. The same bundle submits to either without a code change:
`flwr login supergrid`, `flwr run . supergrid --federation @munibrahman/workspace`.

## 10. Published as a Flower App Bundle on Flower Hub

`flwr build` produces the FAB; `flwr app publish .` shipped v1.0.0 to
`@munibrahman/provenance-harness`.

Verified by round-trip rather than by assumption: `flwr new @munibrahman/provenance-harness`
pulled it back down and all seven files came back byte-identical to local, with `DEMO.md`
correctly excluded by the include patterns. A provenance project that could not reproduce
its own artifact would be self-refuting.

---

## Where Flower is deliberately *not* used

**The verifier.** `provenance_harness/verify.py` imports `hashlib`, `json`, `sys` and
`pathlib` — nothing else. Not the `Ledger` class, not the canonicalizer, not `flwr`.

This is a decision *about* Flower, not an omission. A verifier that imported the writer's
own chain code would only confirm the writer agrees with itself, which proves nothing. The
chain rule is reimplemented from a specification short enough to redo in any language in
about fifteen lines:

```
chain[n]       = sha256( chain[n-1] || canonical_json(entry[n] minus "chain") )
chain[-1]      = "0" * 64
canonical_json = json.dumps(sort_keys=True, separators=(",", ":"))
```

**`local_probe.py`.** Drives the same agent loop straight at an Open Responses endpoint with
no SuperLink, so the loop can be iterated on without the Flower stack in the way — and so
the loop's behaviour can be compared with and without the framework.

---

## What is not there yet, and which Flower capability would fix it

This bundle declares only an `agentapp` component, so it runs in one place — the agent task
— not on each participant's SuperNode. `clientapp-seconds` is 0.0 on every run to date; no
site-side code has ever executed.

That is precisely why this is an **integrity** property and not a **confidentiality** one.
Getting confidentiality would require manifest generation and attestation to run as a
**ClientApp on each SuperNode**, so that only digests return, plus a model endpoint either
inside the trust boundary or one that never sees anything but digests. Neither is true
today, and the README states so rather than leaving it to be inferred.
