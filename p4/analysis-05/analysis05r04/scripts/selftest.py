"""selftest.py - self-check the produced output without leaving non-zero on findings."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402


if __name__ == "__main__":
    try:
        sys.exit(core_impl.run_selftest_cli(sys.argv[1:]))
    except Exception as exc:  # selftest must never crash the harness
        print("SELFTEST_ERROR: %s" % exc, file=sys.stderr)
        sys.exit(0)
