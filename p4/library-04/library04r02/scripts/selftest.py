#!/usr/bin/env python3
"""selftest.py — standalone self-test for the research-kb-qa Skill.

Runs three synthetic scenarios (empty input, unparseable case.json, and a normal
case with output_contract + duplicate + version pair) in a private temp dir, then
asserts result.json validity, contract back-fill, honest status, and verbatim
quote verification. Never touches /input or /app/output.

Usage: python3 scripts/selftest.py [--keep]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def main(argv=None):
    code = core_impl.run_selftest()
    return code


if __name__ == "__main__":
    sys.exit(main())
