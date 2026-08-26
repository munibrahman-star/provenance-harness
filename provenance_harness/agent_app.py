"""Provenance Harness AgentApp.

Every step of the run -- each model request, each model response, each tool
call -- is hashed into an append-only chain and printed as a ledger when the
run finishes. See `provenance_harness/harness.py` for the loop itself.
"""

from __future__ import annotations

from logging import INFO

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import ConfigRecord, Context
from flwr.common.logger import log

from .harness import (
    DEFAULT_INPUT,
    DEFAULT_REQUIRED_ATTESTATIONS,
    Ledger,
    Policy,
    canonical,
    render_report,
    run_harness,
)

app = AgentApp()


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Run the provenance harness against the configured model."""
    cfg = context.run_config
    model = str(cfg.get("agent.model", "")).strip()
    if not model:
        raise ValueError("Run config 'agent.model' must be a non-empty string.")

    user_input = str(cfg.get("agent.input", "")).strip() or DEFAULT_INPUT
    use_tools = bool(cfg.get("agent.tools", True))
    max_turns = int(cfg.get("agent.max-turns", 4))
    required = int(
        cfg.get("agent.required-attestations", DEFAULT_REQUIRED_ATTESTATIONS)
    )

    log(INFO, "[provenance] run_id=%s model=%s tools=%s", context.run_id, model, use_tools)

    def emit(event: dict) -> None:
        try:
            agent.events.emit(event)
        except Exception as err:  # pylint: disable=broad-exception-caught
            log(INFO, "[provenance] event not emitted: %s", err)

    ledger = Ledger(emit=emit)
    result = run_harness(
        agent.responses.create,
        ledger,
        model=model,
        user_input=user_input,
        use_tools=use_tools,
        max_turns=max_turns,
        run_id=str(context.run_id),
        policy=Policy(required_attestations=required),
        logger=lambda msg: log(INFO, "[provenance] %s", msg),
    )

    print(render_report(result), flush=True)
    log(INFO, "[provenance] verdict %s -- %s", result.verdict, result.reason)
    log(INFO, "[provenance] ledger head %s", ledger.head)

    # Persist the ledger into the run context so it outlives the process.
    context.state["provenance"] = ConfigRecord(
        {
            "head": ledger.head,
            "verdict": result.verdict,
            "reason": result.reason,
            "entries": [canonical(entry) for entry in ledger.entries],
        }
    )
