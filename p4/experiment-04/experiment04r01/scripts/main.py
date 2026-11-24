#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py - thin entry wrapper (implementation lives in core_impl.py).

Standard library only. All subcommands are delegated to core_impl.run().
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl.run(sys.argv[1:]))
