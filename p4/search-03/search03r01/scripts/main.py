# -*- coding: utf-8 -*-
"""main.py -- thin entry wrapper for the literature screening Skill.

Subcommands mirror the SKILL.md workflow:

    python3 -B scripts/main.py discover  --input /input --work /app/work --output /app/output
    python3 -B scripts/main.py screen    --work /app/work --output /app/output
    # (Agent fills result.json.results from materials + references/criteria.md)
    python3 -B scripts/main.py finalize  --output /app/output --work /app/work
    python3 -B scripts/main.py selftest  --output /app/output --work /app/work
    python3 -B scripts/main.py run       # discover -> screen -> finalize in one shot

Implementation lives in core_impl.py (kept separate so this entry stays small).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

USAGE = """\
usage: main.py <discover|screen|finalize|selftest|run> [options]

  discover   scan /input, write partial result.json skeleton + normalized case
  screen     deterministic lexical priors + dedup clusters -> scaffold.json
  finalize   validate contract, verify evidence, deterministic rank
  selftest   robustness dry-runs + finalize check
  run        discover -> screen -> finalize (default no-arg behaviour)

options:
  --input DIR    input directory        (default /input)
  --work DIR     working directory      (default /app/work)
  --output DIR   output directory       (default /app/output)
"""


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("--help", "-h"):
        print(USAGE, flush=True)
        return 0
    cmd = args[0]
    rest = args[1:]
    if cmd not in core_impl.COMMANDS and cmd != "run":
        print(USAGE, flush=True)
        return 2
    if cmd == "run":
        # discover -> screen -> finalize; screen/finalize do not accept
        # --input, so strip it (and its value) before passing them on.
        filtered = []
        skip_next = False
        for i, token in enumerate(rest):
            if skip_next:
                skip_next = False
                continue
            if token == "--input" and i + 1 < len(rest):
                skip_next = True
                continue
            if token.startswith("--input="):
                continue
            filtered.append(token)
        rc = core_impl.run_discover(rest)
        if rc == 0:
            rc = core_impl.run_screen(filtered)
        if rc == 0:
            rc = core_impl.run_finalize(filtered)
        return rc
    return core_impl.COMMANDS[cmd](rest)


if __name__ == "__main__":
    sys.exit(main())
