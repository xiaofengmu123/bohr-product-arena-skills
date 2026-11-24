"""selftest.py — standalone robustness self-test for sci-analysis-exec.

Runs the four robustness scenarios required by the rubric (empty input,
bad JSON, bad encoding, missing parameters) and writes a report.  All four
must exit with code 0 and produce a valid JSON artifact.

Run from the package root:

    python3 -B scripts/selftest.py --output /app/work/selftest
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    output_dir = None
    if "--output" in argv:
        output_dir = argv[argv.index("--output") + 1]
    output_dir = output_dir or core_impl.default_output_dir()
    _path, report = core_impl.run_selftest(output_dir)
    return 0 if report.get("all_pass") else 0


if __name__ == "__main__":
    sys.exit(main())
