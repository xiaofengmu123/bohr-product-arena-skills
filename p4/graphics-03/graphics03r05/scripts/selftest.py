#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin wrapper for the package self-test.

Run:  python scripts/selftest.py
Exits 0 when the synthetic example renders and passes the result.json contract
check; otherwise 1. The example is clearly labelled and is never a formal case.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl  # noqa: E402

if __name__ == "__main__":
    sys.exit(core_impl.run_selftest())
