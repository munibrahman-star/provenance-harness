"""Provider-agnostic core of the Provenance Harness.

Kept free of Flower imports so the exact same loop can be driven against a raw
Open Responses endpoint during debugging and against `agent.responses.create`
at run time.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

GENESIS = "0" * 64

CreateFn = Callable[[dict[str, Any]], dict[str, Any]]
EmitFn = Callable[[dict[str, Any]], None]

INSTRUCTIONS = (
    "You are the Provenance Harness agent. Before a federated training round "
    "starts, you must fingerprint every data shard that sites contributed.\n"
    "Use the `attest_artifact` tool to fingerprint a shard. Pass the shard's "
    "short label as `name` (for example \"shard-a\") and the shard's full "
    "manifest string as `content` (for example "
    "\"shard-a:1024rows:cifar10\"). Never invent a hash yourself.\n"
    "Attest every shard mentioned, then reply with a plain-text summary of at "
    "most three sentences. Do not use markdown."
)

DEFAULT_INPUT = (
    "Two sites contributed shards to this round. Site A's manifest is "
    "'shard-a:1024rows:cifar10' and site B's manifest is "
    "'shard-b:768rows:cifar10'. Attest both shards, then tell me whether the "
    "round is reproducible."
)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "attest_artifact",
        "description": (
            "Fingerprint one data shard. 'name' is the shard's short label; "
            "'content' is the shard's full manifest string. Returns a SHA-256 "
            "digest, the byte length, and the ledger sequence number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short label, e.g. 'shard-a'.",
                },
                "content": {
                    "type": "string",
                    "description": "Full manifest string, e.g. "
                    "'shard-a:1024rows:cifar10'.",
                },
            },
            "required": ["name", "content"],
            "additionalProperties": False,
        },
    }
]

# Item types that are safe to replay to the provider on the next turn.
_REPLAYABLE = {"message", "function_call", "function_call_output"}


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def canonical(obj: Any) -> str:
    """Serialize deterministically so digests are reproducible."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


class Ledger:
    """Append-only, hash-chained record of everything the agent did.

    Entry *n*'s ``chain`` is ``sha256(chain[n-1] || canonical_json(entry_n))``,
    so editing any earlier entry invalidates every hash after it.
    """

    def __init__(self, emit: EmitFn | None = None, clock: Callable[[], float] | None = None) -> None:
        import time

        self._emit = emit
        self._clock = clock or time.time
        self._entries: list[dict[str, Any]] = []
        self._t0 = self._clock()

    def append(self, kind: str, actor: str, **fields: Any) -> dict[str, Any]:
        prev = self._entries[-1]["chain"] if self._entries else GENESIS
        entry: dict[str, Any] = {
            "seq": len(self._entries),
            "t": round(self._clock() - self._t0, 3),
            "kind": kind,
            "actor": actor,
            **fields,
        }
        entry["prev"] = prev
        entry["chain"] = hashlib.sha256(
            (prev + canonical(entry)).encode("utf-8")
        ).hexdigest()
        self._entries.append(entry)

        if self._emit is not None:
            self._emit({"type": "provenance.ledger.entry", **entry})
        return entry

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self._entries

    @property
    def head(self) -> str:
        return self._entries[-1]["chain"] if self._entries else GENESIS

    def verify(self) -> tuple[bool, int | None]:
        """Recompute the whole chain; return (ok, first_bad_seq)."""
        prev = GENESIS
        for entry in self._entries:
            body = {k: v for k, v in entry.items() if k != "chain"}
            expected = hashlib.sha256(
                (prev + canonical(body)).encode("utf-8")
            ).hexdigest()
            if expected != entry["chain"] or entry["prev"] != prev:
                return False, int(entry["seq"])
            prev = entry["chain"]
        return True, None

    def render(self, width: int = 100) -> str:
        rule = "=" * width
        lines = [rule, "PROVENANCE LEDGER", rule]
        lines.append(
            f"{'SEQ':>3}  {'T+s':>7}  {'KIND':<18} {'ACTOR':<9} {'CHAIN':<12} DETAIL"
        )
        lines.append("-" * width)
        for entry in self._entries:
            detail = {
                k: v
                for k, v in entry.items()
                if k not in ("seq", "t", "kind", "actor", "prev", "chain")
            }
            lines.append(
                f"{entry['seq']:>3}  {entry['t']:>7.3f}  {entry['kind']:<18} "
                f"{entry['actor']:<9} {str(entry['chain'])[:12]:<12} "
                f"{canonical(detail)}"
            )
        lines.append("-" * width)
        ok, bad = self.verify()
        lines.append(f"entries        : {len(self._entries)}")
        lines.append(f"ledger head    : {self.head}")
        lines.append(
            "chain integrity: VERIFIED"
            if ok
            else f"chain integrity: BROKEN at seq={bad}"
        )
        lines.append(rule)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Locally executed tools
# ---------------------------------------------------------------------------


def attest_artifact(arguments: dict[str, Any], ledger_seq: int) -> dict[str, Any]:
    name = str(arguments.get("name", "<unnamed>"))
    content = str(arguments.get("content", ""))
    return {
        "artifact": name,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "bytes": len(content.encode("utf-8")),
        "ledger_seq": ledger_seq,
    }


TOOLS: dict[str, Callable[[dict[str, Any], int], dict[str, Any]]] = {
    "attest_artifact": attest_artifact,
}


# ---------------------------------------------------------------------------
# Open Responses payload helpers
# ---------------------------------------------------------------------------


def output_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    output = response.get("output")
    if isinstance(output, list):
        return [item for item in output if isinstance(item, dict)]
    return []


def message_text(response: dict[str, Any]) -> str:
    """Extract assistant text from an Open Responses payload."""
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: list[str] = []
    for item in output_items(response):
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
    return "\n".join(c for c in chunks if c.strip()).strip()


def usage_of(response: dict[str, Any]) -> dict[str, Any]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return {}
    return {
        k: usage.get(k)
        for k in ("input_tokens", "output_tokens", "total_tokens")
        if isinstance(usage.get(k), int)
    }


# ---------------------------------------------------------------------------
# Tool-call extraction
# ---------------------------------------------------------------------------

_XML_CALL = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_XML_FN = re.compile(r"<function=([^>\s]+)\s*>(.*)", re.DOTALL)
_XML_PARAM = re.compile(r"<parameter=([^>\s]+)\s*>(.*?)</parameter>", re.DOTALL)


def _parse_text_tool_calls(text: str) -> list[dict[str, Any]]:
    """Recover tool calls a provider left unparsed in the message body.

    vLLM only turns model output into `function_call` items when it is started
    with a tool-call parser. Without one, Qwen emits its raw `<tool_call>`
    block as assistant text; both the XML and the JSON dialect are handled.
    """
    calls: list[dict[str, Any]] = []
    for block in _XML_CALL.findall(text or ""):
        block = block.strip()

        # Hermes dialect: <tool_call>{"name": ..., "arguments": {...}}</tool_call>
        if block.startswith("{"):
            try:
                payload = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("name"), str):
                calls.append(
                    {
                        "name": payload["name"],
                        "arguments": payload.get("arguments") or {},
                    }
                )
            continue

        # Qwen XML dialect: <function=NAME><parameter=KEY>VALUE</parameter>...
        fn = _XML_FN.match(block)
        if fn is None:
            continue
        arguments = {
            key: value.strip() for key, value in _XML_PARAM.findall(fn.group(2))
        }
        calls.append({"name": fn.group(1).strip(), "arguments": arguments})
    return calls


def _strip_tool_calls(text: str) -> str:
    return _XML_CALL.sub("", text or "").strip()


def extract_tool_calls(response: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Return (tool_calls, recovered_from_text).

    Each call is normalized to ``{"call_id", "name", "arguments"}`` with
    ``arguments`` already decoded into a dict.
    """
    native: list[dict[str, Any]] = []
    for item in output_items(response):
        if item.get("type") != "function_call":
            continue
        raw = item.get("arguments")
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            arguments = {}
        native.append(
            {
                "call_id": str(item.get("call_id") or item.get("id") or ""),
                "name": str(item.get("name", "")),
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        )
    if native:
        return native, False

    recovered = []
    for index, call in enumerate(_parse_text_tool_calls(message_text(response))):
        seed = f"{response.get('id', '')}:{index}:{canonical(call)}"
        recovered.append(
            {
                "call_id": f"call_{hashlib.sha256(seed.encode()).hexdigest()[:16]}",
                "name": call["name"],
                "arguments": call["arguments"],
            }
        )
    return recovered, bool(recovered)


# ---------------------------------------------------------------------------
# The harness loop
# ---------------------------------------------------------------------------


def run_harness(  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
    create: CreateFn,
    ledger: Ledger,
    *,
    model: str,
    user_input: str,
    use_tools: bool = True,
    max_turns: int = 4,
    run_id: str = "local",
    logger: Callable[[str], None] = lambda _msg: None,
) -> tuple[str, Ledger]:
    """Drive the agent loop, recording every step into `ledger`."""
    ledger.append(
        "run.started",
        "harness",
        run_id=run_id,
        model=model,
        tools=use_tools,
        input_sha256=digest(user_input)[:16],
    )

    items: list[dict[str, Any]] = [
        {"type": "message", "role": "user", "content": user_input}
    ]
    final_text = ""
    completed = False

    for turn in range(1, max_turns + 1):
        request: dict[str, Any] = {
            "model": model,
            "instructions": INSTRUCTIONS,
            "input": list(items),
            "stream": False,
        }
        if use_tools:
            request["tools"] = TOOL_SCHEMAS
            request["tool_choice"] = "auto"

        ledger.append(
            "model.request",
            "harness",
            turn=turn,
            items=len(items),
            tools=len(request.get("tools", [])),
            request_sha256=digest(request)[:16],
        )
        logger(f"turn {turn}/{max_turns} -> model")

        try:
            response = create(request)
        except Exception as err:  # pylint: disable=broad-exception-caught
            ledger.append("model.error", "provider", turn=turn, error=str(err)[:400])
            if use_tools:
                # Some providers reject tool schemas outright; degrade, don't die.
                use_tools = False
                ledger.append(
                    "harness.degraded",
                    "harness",
                    turn=turn,
                    reason="provider rejected the request with tools; retrying tool-free",
                )
                logger(f"turn {turn} failed with tools; retrying tool-free")
                continue
            raise

        produced = output_items(response)
        calls, recovered = extract_tool_calls(response)

        ledger.append(
            "model.response",
            "provider",
            turn=turn,
            response_id=str(response.get("id", "")),
            status=str(response.get("status", "")),
            output_items=len(produced),
            tool_calls=len(calls),
            usage=usage_of(response),
            response_sha256=digest(response)[:16],
        )

        if recovered:
            ledger.append(
                "provider.unparsed_tool_calls",
                "harness",
                turn=turn,
                count=len(calls),
                note="provider returned tool calls as text; recovered by harness",
            )

        if not calls:
            final_text = _strip_tool_calls(message_text(response))
            completed = True
            break

        # Replay only what the provider can accept back as input.
        if recovered:
            for call in calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["call_id"],
                        "name": call["name"],
                        "arguments": canonical(call["arguments"]),
                    }
                )
        else:
            items.extend(
                item for item in produced if item.get("type") in _REPLAYABLE
            )

        for call in calls:
            tool_fn = TOOLS.get(call["name"])
            if tool_fn is None:
                result: dict[str, Any] = {"error": f"unknown tool '{call['name']}'"}
            else:
                result = tool_fn(call["arguments"], len(ledger.entries))

            ledger.append(
                "tool.call",
                "harness",
                turn=turn,
                tool=call["name"],
                call_id=call["call_id"],
                arguments_sha256=digest(call["arguments"])[:16],
                result=result,
            )
            logger(f"tool {call['name']} -> {canonical(result)}")
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": canonical(result),
                }
            )

    if not completed:
        ledger.append("harness.exhausted", "harness", max_turns=max_turns)

    ledger.append(
        "run.completed",
        "harness",
        answer_chars=len(final_text),
        answer_sha256=digest(final_text)[:16],
    )
    return final_text, ledger


def render_report(ledger: Ledger, final_text: str, width: int = 100) -> str:
    return "\n".join(
        [
            "",
            ledger.render(width),
            "",
            "AGENT ANSWER",
            "-" * width,
            final_text or "<no text returned>",
            "-" * width,
        ]
    )
