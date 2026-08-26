"""Drive the harness loop straight against an Open Responses endpoint.

Debug aid only -- it bypasses Flower so the agent loop can be iterated on
without a SuperLink in the way. Usage:

    FLWR_MODEL_API_ENDPOINT=http://host:8001/v1/responses python local_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

from provenance_harness.harness import (
    DEFAULT_INPUT,
    Ledger,
    render_report,
    run_harness,
)

ENDPOINT = os.environ.get("FLWR_MODEL_API_ENDPOINT", "").strip()
MODEL = os.environ.get("PROBE_MODEL", "").strip()

if not ENDPOINT or not MODEL:
    raise SystemExit(
        "Set FLWR_MODEL_API_ENDPOINT (must end in /responses) and PROBE_MODEL "
        "before running the probe, e.g.\n"
        "  FLWR_MODEL_API_ENDPOINT=http://HOST:PORT/v1/responses \\\n"
        "  PROBE_MODEL=/models/YourModel python3 local_probe.py"
    )


def create(request: dict) -> dict:
    body = json.dumps(request).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ledger = Ledger()
    final_text, ledger = run_harness(
        create,
        ledger,
        model=MODEL,
        user_input=DEFAULT_INPUT,
        use_tools=os.environ.get("PROBE_TOOLS", "1") != "0",
        max_turns=int(os.environ.get("PROBE_MAX_TURNS", "4")),
        run_id="local-probe",
        logger=lambda msg: print(f"[probe] {msg}", flush=True),
    )
    print(render_report(ledger, final_text))
    ok, bad = ledger.verify()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
