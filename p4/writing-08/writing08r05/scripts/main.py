#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry wrapper for the methods-experiments-writing Skill.

All implementation lives in core_impl.py next to this file. The wrapper only
resolves the sibling module so the package runs identically from any cwd.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def main():
    core_impl.run(sys.argv[1:])


if __name__ == "__main__":
    main()
