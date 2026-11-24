# -*- coding: utf-8 -*-
"""main.py -- thin CLI entry for the stat-analysis-02-evidence skill.

All implementation lives in core_impl.py.  The entry only resolves paths and
dispatches a subcommand so that SKILL.md can call it with relative paths:

    cd /skills/weekly-skill && python scripts/main.py run
    python scripts/main.py init | survey | inspect | analyze | build | selfcheck
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

HELP = """usage: python scripts/main.py [run|init|survey|inspect|analyze|build|selfcheck]
              [--input DIR] [--output DIR] [--work DIR] [--help]

Default subcommand is `run` (init -> survey -> inspect -> analyze -> build -> selfcheck).
Defaults: --input /input --output /app/output --work /app/work
"""


def parse_args(argv):
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd in ("--help", "-h"):
        cmd = "help"
    input_dir, output_dir, work_dir = DEFAULT_INPUT, DEFAULT_OUTPUT, DEFAULT_WORK
    i = 2
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "-i") and i + 1 < len(argv):
            input_dir = argv[i + 1]
            i += 2
            continue
        if a in ("--output", "-o") and i + 1 < len(argv):
            output_dir = argv[i + 1]
            i += 2
            continue
        if a in ("--work", "-w") and i + 1 < len(argv):
            work_dir = argv[i + 1]
            i += 2
            continue
        if a in ("--help", "-h"):
            cmd = "help"
            i += 1
            continue
        i += 1
    return cmd, input_dir, output_dir, work_dir


def main():
    cmd, input_dir, output_dir, work_dir = parse_args(sys.argv)
    if cmd == "help":
        print(HELP)
        raise SystemExit(0)
    sys.path.insert(0, HERE)
    import core_impl  # noqa: E402
    core_impl.run(cmd, input_dir, output_dir, work_dir)


if __name__ == "__main__":
    main()
