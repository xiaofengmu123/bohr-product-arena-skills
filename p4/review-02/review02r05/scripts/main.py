# -*- coding: utf-8 -*-
"""main.py — thin entry dispatcher for citation-evidence-audit.

The implementation lives in core_impl.py so that this entry file stays small
and easy to audit. Invoke with a subcommand:

    python -B scripts/main.py run --input /input --output /app/output --work /app/work
    python -B scripts/main.py discover | scaffold | finalize | check
    python -B scripts/main.py --help
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core_impl  # noqa: E402


def main(argv=None) -> int:
    parser = core_impl.build_parser()
    args = parser.parse_args(argv)
    return core_impl.dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
