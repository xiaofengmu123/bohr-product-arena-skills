#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry wrapper for the research-frontier-trend skill.

The heavy lifting lives in core_impl.py; this file only dispatches subcommands so
it stays small and reviewable.  All paths default to the runtime contract
(/input, /app/output, /app/work) and can be overridden for local testing.

Subcommands
    init      scan /input, build the evidence ledger and a partial result.json
    extract   deterministic feature extraction (years / terms / candidates)
    compile   merge ledger + features + claims.json into final artifacts
    selftest  validate ledger, verbatim quotes and result.json structure
    status    print a compact pipeline status summary
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.main())
