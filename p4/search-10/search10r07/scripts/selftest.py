#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — robustness self-check for the cross-lingual search skill.

Runs the scenarios in core.self_test() and exits 0 when all pass, 1 otherwise.
Standard library only; no network, no real case answers, no benchmark scores.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402


def main():
    ok, report = core.self_test()
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
