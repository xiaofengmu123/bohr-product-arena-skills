#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — scix-plan-evidence 的薄入口，实际实现见 core_impl.py。

用法：
  python3 scripts/main.py input_probe --input /input --work /app/work
  python3 scripts/main.py scaffold    --input /input --output /app/output --work /app/work
  python3 scripts/main.py validate    --input /input --output /app/output --work /app/work
  python3 scripts/main.py selftest    [--work DIR]
  python3 scripts/main.py --help
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py <input_probe|scaffold|validate|selftest> [options]")
    print("        --input DIR   (默认 /input)")
    print("        --output DIR  (默认 /app/output)")
    print("        --work DIR    (默认 /app/work)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _help()
        return 0
    sys.path.insert(0, HERE)
    import core_impl  # noqa: E402
    return core_impl.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
