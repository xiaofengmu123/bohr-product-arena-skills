#!/usr/bin/env python3
"""main.py — thin entry point for the research-kb-qa Skill.

All implementation lives in core_impl.py (kept separate so this wrapper stays
small). Usage: python3 scripts/main.py <scaffold|inventory|finalize|mark|selftest>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def main(argv=None):
    parser = core_impl.build_parser()
    args = parser.parse_args(argv)
    code = core_impl.run(args.cmd, args)
    return code if isinstance(code, int) else 0


if __name__ == "__main__":
    sys.exit(main())
