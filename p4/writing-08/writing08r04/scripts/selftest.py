#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone self-test for the Skill package.

Run from the skill root:  python3 scripts/selftest.py
Exercises the pipeline on synthetic inputs (normal, empty, bad JSON) and
exits 0 only when the scripts behave per contract.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import run_selftest

if __name__ == "__main__":
    sys.exit(run_selftest())
