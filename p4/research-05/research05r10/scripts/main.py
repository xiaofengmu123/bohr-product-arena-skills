"""CLI entry for the research-selection-audit skill.

Usage examples (relative paths as documented in SKILL.md):
    python scripts/main.py init    --input /input --output /app/output --work /app/work
    python scripts/main.py scan    --input /input --output /app/output --work /app/work
    python scripts/main.py evidence --source m01 --locator "paper.txt:3" \
        --quote "verbatim sentence from the material" --facet prior_work --status fact
    python scripts/main.py log --msg "recorded a change"
    python scripts/main.py build   --input /input --output /app/output --work /app/work
    python scripts/main.py selftest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (  # noqa: E402
    build_parser,
    cmd_build,
    cmd_evidence,
    cmd_init,
    cmd_log,
    cmd_scan,
    cmd_selftest,
)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    kwargs = vars(args)
    fn = kwargs.pop("func")
    kwargs.pop("command", None)
    return fn(**kwargs)


if __name__ == "__main__":
    sys.exit(main())
