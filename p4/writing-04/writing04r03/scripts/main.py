"""Entry point for the reference & citation management skill.

Usage:
    python3 -B scripts/main.py [--input /input] [--output /app/output]
                               [--work /app/work] [--style gbt7714|apa]
                               [--self-test]

Behavior contract (critical for the 900 s / 64-request harness budget):

1.  Immediately create /app/output and write a valid ``result.json`` skeleton
    plus editable artifacts, so any later interruption never leaves an empty
    or malformed delivery.
2.  Run the deterministic backbone (discover -> parse -> deduplicate ->
    renumber -> format -> consistency check) in :mod:`core_impl`.
3.  Always exit 0 with an honest status.  Semantic judgments (claim-level
    citation placement, cross-language dedup confirmation, source
    verification) are performed by the Agent following SKILL.md; this script
    never fabricates them.

``--self-test`` delegates to :mod:`selftest` and returns its exit code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic reference & citation management backbone.",
    )
    p.add_argument("--input", default="/input",
                   help="input directory (default: /input)")
    p.add_argument("--output", default="/app/output",
                   help="output directory (default: /app/output)")
    p.add_argument("--work", default="/app/work",
                   help="work directory (default: /app/work)")
    p.add_argument("--style", choices=["gbt7714", "apa"], default="gbt7714",
                   help="reference formatting style (default: gbt7714)")
    p.add_argument("--self-test", action="store_true",
                   help="run the built-in determinism/degradation self-test")
    return p.parse_args(argv)


def load_citation_map(work_dir: Path) -> dict | None:
    """Read an optional citation_map.json the Agent wrote into /app/work."""
    path = work_dir / "citation_map.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv) if argv is not None else sys.argv[1:])

    if args.self_test:
        import selftest
        return selftest.run()

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    result = ce.run_pipeline(
        input_dir=args.input,
        output_dir=args.output,
        work_dir=args.work,
        style=args.style,
        citation_map=load_citation_map(work),
    )
    print(f"case_id={result.get('case_id')!r}")
    print(f"status={result.get('status')!r}")
    print(f"artifacts={len(result.get('artifacts', {}))}")
    print(f"errors={len(result.get('errors', []))} "
          f"degradations={len(result.get('degradations', []))}")
    print(f"content_hash={result.get('content_hash')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
