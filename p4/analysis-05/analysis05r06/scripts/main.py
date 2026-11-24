"""main.py -- thin entry wrapper for the multi-step data analysis skill.

The implementation lives in core_impl.py (stdlib only). This file is kept
small so it can be audited quickly; it only resolves the script directory,
adds it to sys.path, and delegates.
"""
import os
import sys


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import core_impl
    except ImportError as exc:  # pragma: no cover
        print(f"[error] cannot import core_impl.py: {exc}", file=sys.stderr)
        return 1
    return core_impl.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
