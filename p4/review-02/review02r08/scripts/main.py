# -*- coding: utf-8 -*-
"""main.py -- CLI entry for citation-fact-audit-v2.

usage: main.py [discover|scaffold|finalize] [--input DIR] [--output DIR] [--work DIR] [--help]
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402

_USAGE = (
    "usage: main.py {discover|scaffold|finalize} "
    "[--input DIR] [--output DIR] [--work DIR] [--help]\n"
    "defaults: --input /input --output /app/output --work /app/work\n"
)


def main(argv):
    if "--help" in argv or "-h" in argv or len(argv) < 2:
        sys.stdout.write(_USAGE)
        return 0

    cmd = argv[1]
    rest = argv[2:]
    input_dir, output_dir, work_dir, want_help = core_impl.parse_common_args(rest)
    if want_help:
        sys.stdout.write(_USAGE)
        return 0

    if cmd == "discover":
        m = core_impl.discover(input_dir, output_dir, work_dir)
        print(json_summary(m))
    elif cmd == "scaffold":
        r = core_impl.scaffold(input_dir, output_dir, work_dir)
        print("scaffold: status=%s verdict=%s case_id=%s" % (
            r["status"], r["summary"]["verdict"], r["case_id"]))
    elif cmd == "finalize":
        r = core_impl.finalize(input_dir, output_dir, work_dir)
        print("finalize: status=%s verdict=%s total=%s" % (
            r["status"], r["summary"]["verdict"],
            r["summary"]["counts"]["total"]))
    else:
        sys.stderr.write("unknown command: %s\n%s" % (cmd, _USAGE))
        return 2
    return 0


def json_summary(m):
    import json
    return json.dumps({
        "case_id": m.get("case_id"),
        "files": len(m.get("files", [])),
        "materials": len(m.get("materials", [])),
        "errors": m.get("errors", []),
        "degradations": m.get("degradations", []),
    }, ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
