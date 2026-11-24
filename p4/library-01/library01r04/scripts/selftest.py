#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — standalone self-check for the document-library repair Skill.

Builds a small synthetic library (duplicates, version chain, broken symlink,
bad JSON, bad encoding, empty dir, catalog with a missing reference) in a
temporary directory, runs the full pipeline, and asserts that:

  * result.json exists with a non-empty case_id and an allowed status;
  * the output_contract fields are backfilled;
  * duplicates, version candidates and a broken symlink are detected;
  * bad JSON / bad encoding are recorded in degradations (never crash);
  * every editable artifact is produced;
  * `--check` passes.

Exit code 0 means the package is loadable and the engine runs end-to-end.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(core_impl.selftest())
