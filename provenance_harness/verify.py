"""Standalone verifier for a provenance ledger.

Deliberately self-contained. It imports nothing from the rest of this package
-- not the Ledger class, not the canonicalizer, not the chain rule. It
reimplements the rule from the specification below using only the standard
library, so that a bug in the app cannot hide itself by also being present in
its own verifier. If the two disagree, that disagreement is the finding.

The rule, in full:

    chain[n] = sha256( chain[n-1] || canonical_json(entry[n] minus "chain") )
    chain[-1] = "0" * 64                      (the genesis value)
    canonical_json = json.dumps(sort_keys=True, separators=(",", ":"))

Input is JSON Lines: one ledger entry object per line.

Exit status: 0 if the chain verifies, 1 otherwise.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
WIDTH = 78


def canonical_json(obj: Any) -> str:
    """Serialize deterministically. Must match the writer byte for byte."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def recompute(prev_chain: str, entry: dict[str, Any]) -> str:
    """Recompute one entry's chain value from its contents and its parent."""
    body = {key: value for key, value in entry.items() if key != "chain"}
    return hashlib.sha256(
        (prev_chain + canonical_json(body)).encode("utf-8")
    ).hexdigest()


def load(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Read JSONL entries. Returns (entries, error_message)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as err:
        return [], f"cannot read {path}: {err}"

    entries: list[dict[str, Any]] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as err:
            return [], f"line {lineno} is not valid JSON: {err}"
        if not isinstance(parsed, dict):
            return [], f"line {lineno} is not a JSON object"
        for required in ("seq", "chain", "prev"):
            if required not in parsed:
                return [], f"line {lineno} has no '{required}' field"
        entries.append(parsed)

    if not entries:
        return [], f"{path} contains no ledger entries"
    return entries, None


def describe(entry: dict[str, Any]) -> str:
    kind = entry.get("kind", "?")
    actor = entry.get("actor", "?")
    return f"kind={kind} actor={actor}"


def verify(entries: list[dict[str, Any]]) -> tuple[bool, int | None, str, str, str]:
    """Walk the chain.

    Returns (ok, bad_index, expected, found, why).
    """
    prev = GENESIS
    for index, entry in enumerate(entries):
        # The recorded parent must be the previous entry's chain value.
        if str(entry.get("prev")) != prev:
            return (
                False,
                index,
                prev,
                str(entry.get("prev")),
                "this entry's recorded parent hash is not the previous "
                "entry's chain value -- the chain has been relinked",
            )

        expected = recompute(prev, entry)
        found = str(entry.get("chain"))
        if expected != found:
            return (
                False,
                index,
                expected,
                found,
                "this entry's contents do not hash to its recorded chain "
                "value -- the entry has been edited",
            )
        prev = found

    return True, None, "", "", ""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print("usage: verify <ledger.jsonl>", file=sys.stderr)
        print(
            "\nVerifies a provenance ledger's hash chain. "
            "Exits 0 if intact, 1 if not.",
            file=sys.stderr,
        )
        return 1

    path = Path(argv[0]).expanduser()
    entries, error = load(path)
    if error is not None:
        print("=" * WIDTH)
        print("LEDGER UNREADABLE")
        print("=" * WIDTH)
        print(f"  {error}")
        print("=" * WIDTH)
        return 1

    total = len(entries)
    ok, bad, expected, found, why = verify(entries)

    if ok:
        print("=" * WIDTH)
        print("VERIFIED")
        print("=" * WIDTH)
        print(f"  file          : {path}")
        print(f"  entries       : {total}")
        print(f"  genesis       : {GENESIS[:16]}... (all zeroes)")
        print(f"  ledger head   : {entries[-1]['chain']}")
        print()
        print("  Every entry hashes to its recorded chain value, and every")
        print("  chain value is built on the one before it, back to genesis.")
        print("=" * WIDTH)
        return 0

    assert bad is not None
    trusted = bad  # entries [0, bad) verified before the break
    print("=" * WIDTH)
    print("LEDGER BROKEN")
    print("=" * WIDTH)
    print(f"  file            : {path}")
    print(f"  entries         : {total}")
    print()
    print(f"  first bad entry : #{bad}  ({describe(entries[bad])})")
    print(f"  expected        : {expected}")
    print(f"  found           : {found}")
    print(f"  why             : {why}")
    print()
    if trusted > 0:
        print(
            f"  TRUSTWORTHY     : entries 0..{trusted - 1} "
            f"({trusted} of {total}) verify against genesis"
        )
    else:
        print("  TRUSTWORTHY     : none -- the break is at the first entry")
    print(
        f"  NOT TRUSTWORTHY : entries {bad}..{total - 1} "
        f"({total - bad} of {total}) -- everything at or after the break"
    )
    print()
    print("  A hash chain proves entries were not altered after the fact. It")
    print("  cannot prove the writer was honest when it wrote them: nothing")
    print("  external countersigns this file.")
    print("=" * WIDTH)
    return 1


if __name__ == "__main__":
    sys.exit(main())
