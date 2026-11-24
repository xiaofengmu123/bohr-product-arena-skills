# -*- coding: utf-8 -*-
"""main.py — thin entry wrapper for the document-library repair Skill.

All implementation lives in core_impl.py so this file stays small and readable.
Usage:  python3 -B scripts/main.py [options]
Run with --help for the full option list.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    try:
        sys.exit(core_impl.main())
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:  # final guard
        print("fatal: %s" % e, file=sys.stderr)
        sys.exit(2)
