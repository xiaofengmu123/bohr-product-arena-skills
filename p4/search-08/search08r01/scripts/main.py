# -*- coding: utf-8 -*-
"""main.py - thin entry wrapper for the systematic literature screening skill.

Delegates argument handling and all logic to core_impl.py. Usage:

    python3 -B scripts/main.py <command> [--input DIR] [--output DIR] [--work DIR]
    commands: skeleton | normalize | finalize | selfcheck | all
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.main())
