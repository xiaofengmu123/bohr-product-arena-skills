"""Entry wrapper for the research selection & novelty review skill.

Thin command-line dispatcher; all logic lives in core_impl.py.  Only the
Python standard library is used.

Usage:
  python3 -B scripts/main.py init      --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py discover  --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py record --kind plan --file /app/work/fragments/01_plan.json
  python3 -B scripts/main.py finalize  --input /input --output /app/output --work /app/work
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl  # noqa: E402

USAGE = """\
Usage:
  scripts/main.py init      [--input DIR] [--output DIR] [--work DIR]
  scripts/main.py discover  [--input DIR] [--output DIR] [--work DIR]
  scripts/main.py record --kind {plan|evidence|figure|synthesis} --file FILE
  scripts/main.py finalize  [--input DIR] [--output DIR] [--work DIR]

Defaults: --input /input  --output /app/output  --work /app/work
"""


def parse_args(argv):
    args = {"input": core_impl.DEFAULT_INPUT,
            "output": core_impl.DEFAULT_OUTPUT,
            "work": core_impl.DEFAULT_WORK,
            "kind": None, "file": None}
    i = 0
    while i < len(argv):
        key = argv[i]
        if key in ("--input", "--output", "--work", "--kind", "--file"):
            if i + 1 >= len(argv):
                return None, "missing value for %s" % key
            args[key[2:]] = argv[i + 1]
            i += 2
        else:
            return None, "unknown argument: %s" % key
    return args, None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0
    cmd = argv[0]
    args, err = parse_args(argv[1:])
    if err:
        sys.stderr.write(err + "\n" + USAGE)
        return 2

    if cmd == "init":
        return core_impl.cmd_init(args["input"], args["output"], args["work"])
    if cmd == "discover":
        return core_impl.cmd_discover(args["input"], args["output"], args["work"])
    if cmd == "record":
        if not args["kind"] or not args["file"]:
            sys.stderr.write("record requires --kind and --file\n" + USAGE)
            return 2
        return core_impl.cmd_record(args["input"], args["output"], args["work"],
                                    args["kind"], args["file"])
    if cmd == "finalize":
        return core_impl.cmd_finalize(args["input"], args["output"], args["work"])

    sys.stderr.write("unknown command: %s\n" % cmd + USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
