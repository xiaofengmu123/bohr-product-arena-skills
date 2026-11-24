"""main.py — thin command-line entry for the auditable literature-screening skill.

Subcommands:
  discover   discover inputs, normalize case, compile plan.json, write result skeleton
  scaffold   deterministic lexical signals + dedupe clustering + evidence-ledger template
  finalize   merge plan x scaffold x evidence_ledger, validate, rank, write result.json
  selftest   run the built-in self-test (delegates to selftest.py)
  (no arg)   run the deterministic discover -> scaffold -> finalize pipeline

All logic lives in core_impl.py. This file is intentionally thin (<30 KB).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as impl  # noqa: E402


def _usage():
    return (
        "usage: python -B scripts/main.py [discover|scaffold|finalize|selftest] "
        "[--input DIR] [--work DIR] [--output DIR]\n"
        "defaults: --input /input --work /app/work --output /app/output"
    )


def _parse_args(argv):
    args = {"input": "/input", "work": "/app/work", "output": "/app/output", "cmd": ""}
    i = 0
    if argv and argv[0] in ("discover", "scaffold", "finalize", "selftest"):
        args["cmd"] = argv[0]
        i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "-i") and i + 1 < len(argv):
            args["input"] = argv[i + 1]
            i += 2
        elif a in ("--work", "-w") and i + 1 < len(argv):
            args["work"] = argv[i + 1]
            i += 2
        elif a in ("--output", "-o") and i + 1 < len(argv):
            args["output"] = argv[i + 1]
            i += 2
        elif a in ("--help", "-h"):
            print(_usage())
            sys.exit(0)
        else:
            print(_usage())
            sys.exit(2)
    return args


def main(argv):
    args = _parse_args(argv)
    cmd = args["cmd"]

    if cmd == "selftest":
        import selftest
        return selftest.main([])

    if cmd == "discover":
        info = impl.discover(args["input"], args["work"], args["output"])
        print("discover ok: candidates=%d criteria=%d input_error=%s"
              % (info["candidate_count"], info["criteria_count"], info["input_error"]))
        if info["errors"]:
            print("errors:")
            for e in info["errors"]:
                print("  - " + e)
        return 0

    if cmd == "scaffold":
        info = impl.scaffold(args["work"], args["output"])
        if info.get("error"):
            print("scaffold failed: " + info["error"])
            return 1
        print("scaffold ok: records=%d dedupe_groups=%d" % (info["records"], info["dedupe_groups"]))
        return 0

    if cmd == "finalize":
        errors, warnings, status = impl.finalize(args["work"], args["output"])
        print("finalize ok: status=%s errors=%d warnings=%d" % (status, len(errors), len(warnings)))
        for w in warnings:
            print("  WARN: " + w)
        for e in errors:
            print("  ERROR: " + e)
        return 0 if not errors else 1

    # Default: full deterministic pipeline (never blocks on the model).
    result = impl.full_pipeline(args["input"], args["work"], args["output"])
    print("pipeline ok: status=%s errors=%d warnings=%d"
          % (result["status"], len(result["errors"]), len(result["warnings"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
