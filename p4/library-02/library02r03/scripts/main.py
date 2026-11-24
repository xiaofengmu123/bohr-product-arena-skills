"""main.py - thin entry wrapper (implementation lives in core_impl.py).

Kept small on purpose: all real logic is in core_impl.py so this file stays
well under the size guideline and is trivially auditable.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _help() -> str:
    return (
        "usage: main.py [--case-json PATH] [--task-path PATH] [--input DIR]\n"
        "              [--output DIR] [--work DIR] [--skeleton-only]\n"
        "              [--update PATCH.json --result PATH] [--check PATH] [--help]\n"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(_help())
        raise SystemExit(0)
    raise SystemExit(core_impl.main())
