# -*- coding: utf-8 -*-
"""Standalone determinism & robustness self-check.

Runs the script pipeline twice on a synthetic deterministic input and asserts
the content_sha256 is identical across runs, plus empty-input and bad-JSON
degradation paths. Exits 0 on pass, 1 on fail. Standard library only.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C  # noqa: E402


def main(argv=None):
    root = ""
    if argv:
        root = argv[0]
    passed, lines = C.run_selftest(root or None)
    for line in lines:
        print(line)
    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
