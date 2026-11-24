#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin entry wrapper. Kept intentionally small (< 30KB); all engine logic lives
in core_impl.py. Runs from the package root via:
    python3 -B scripts/main.py <subcommand> --input /input --output /app/output --work /app/work
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from core_impl import main as _main
except Exception as _exc:  # pragma: no cover - degraded but still exits 0
    sys.stderr.write("core_impl import failed: %s\n" % _exc)
    import json

    def _main(argv=None):
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
        p.add_argument("cmd", nargs="?")
        a = p.parse_args(argv)
        try:
            os.makedirs(a.output, exist_ok=True)
            with open(os.path.join(a.output, "result.json"), "w", encoding="utf-8") as f:
                json.dump({"status": "input_error", "errors": ["core_impl import failed: %s" % _exc],
                           "sources": [], "uncertainty": [], "change_log": []}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(_main())
