#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- thin CLI entry for the coverage/omission analysis Skill.

Implementation lives in core_impl.py (stdlib only). Kept deliberately small.
Usage:  python scripts/main.py <scaffold|inspect|run|merge|selftest> [--help]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    sys.exit(core_impl.main())
