"""selftest.py — self-test for the document library repair Skill.

Runs a synthetic case (duplicates, versions, broken symlink, catalog mismatch,
questions) and asserts the pipeline produces a valid result.json and all
editable artifacts, and that re-running is deterministic. Uses only the
standard library and a temporary directory.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl.selftest())
