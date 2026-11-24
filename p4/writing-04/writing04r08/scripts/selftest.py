"""Standalone self-test for the citation-management skill (v2).

Runs the same deterministic in-memory checks as `main.py --self-test`
and exits 0 on success / 1 on failure.  Standard library only.

    python3 -B scripts/selftest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce  # noqa: E402


def main() -> int:
    ok = ce.run_self_test()
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
