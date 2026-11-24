# -*- coding: utf-8 -*-
"""Command-line entry point for the literature-tracking skill.

Standard library only; paths are relative to the skill root (e.g.
/skills/weekly-skill). The scripts perform only deterministic bookkeeping —
skeleton, inventory, dedup, normalization and finalize. Substantive reading
and scientific judgment are performed by the Agent, which writes its
conclusions into a findings.json consumed by ``finalize``.

Examples::

    python3 scripts/main.py --help
    python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
    python3 scripts/main.py run      --input /input --output /app/output --work /app/work
    python3 scripts/main.py finalize --input /input --output /app/output --work /app/work \
                                     --findings /app/work/findings.json
    python3 scripts/main.py dedup    --records /app/work/records.json --out /app/work/dedup.json
    python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}


def _common():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--input", default=DEFAULTS["input"],
                   help="input dir (default: %(default)s)")
    p.add_argument("--output", default=DEFAULTS["output"],
                   help="output dir (default: %(default)s)")
    p.add_argument("--work", default=DEFAULTS["work"],
                   help="work dir (default: %(default)s)")
    return p


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="Literature-tracking skill: deterministic skeleton/dedup/finalize.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("scaffold", parents=[_common()],
                   help="write a valid partial result.json skeleton")
    sub.add_parser("run", parents=[_common()],
                   help="scaffold + material inventory + empty finalize")

    fp = sub.add_parser("finalize", parents=[_common()],
                        help="merge findings and write result.json + report.md")
    fp.add_argument("--findings", required=True,
                    help="path to Agent findings.json")

    dp = sub.add_parser("dedup", parents=[_common()],
                        help="dedup/version-group a records.json")
    dp.add_argument("--records", required=True, help="records.json with list of records")
    dp.add_argument("--out", required=True, help="output dedup.json path")

    np = sub.add_parser("normalize", parents=[_common()],
                        help="print normalized canonical key for one record")
    np.add_argument("--record", required=True, help="JSON object string")

    sp = sub.add_parser("inventory", parents=[_common()],
                        help="print discovered input manifest as JSON")
    sp.add_argument("--out", default=None, help="optional path to write manifest")

    return p.parse_args(argv)


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def cmd_scaffold(args):
    case_id, contract, data, errors = core.extract_case(args.input)
    skeleton = core.make_skeleton(case_id, contract,
                                  status=core._initial_status(data, errors),
                                  errors=errors)
    skeleton["_contract_fields"] = list(contract)
    core.append_change_log(skeleton, "contract_discovered",
                           {"fields": contract, "source": data.get("case_path")})
    skeleton.pop("_contract_fields", None)
    _ensure_dir(args.output)
    out = os.path.join(args.output, "result.json")
    core.write_json(out, skeleton)
    print(json.dumps({"case_id": case_id, "status": skeleton["status"],
                      "contract_fields": contract, "result": out,
                      "errors": errors}, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args):
    case_id, contract, data, errors = core.extract_case(args.input)
    _ensure_dir(args.output)
    _ensure_dir(args.work)
    # Deterministic material inventory (names + sizes only, no content parse).
    manifest = {
        "case_id": case_id,
        "contract_fields": contract,
        "case_path": data.get("case_path"),
        "task_path": data.get("task_path"),
        "materials": [
            {"path": p, "size": os.path.getsize(p) if os.path.isfile(p) else None}
            for p in data.get("materials", [])
        ],
        "errors": errors,
    }
    manifest_path = os.path.join(args.work, "inventory.json")
    core.write_json(manifest_path, manifest)

    # Skeleton with honest status.
    skeleton = core.make_skeleton(case_id, contract,
                                  status=core._initial_status(data, errors),
                                  errors=errors)
    core.append_change_log(skeleton, "inventory_written",
                           {"path": manifest_path,
                            "material_count": len(data.get("materials", []))})
    out = os.path.join(args.output, "result.json")
    core.write_json(out, skeleton)
    print(json.dumps({"case_id": case_id, "status": skeleton["status"],
                      "inventory": manifest_path, "result": out},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_finalize(args):
    result, rpath, rpath2, errors = core.finalize(
        args.input, args.output, args.work, args.findings)
    print(json.dumps({"case_id": result.get("case_id"),
                      "status": result.get("status"),
                      "result": rpath, "report": rpath2,
                      "errors": errors}, ensure_ascii=False, indent=2))
    return 0


def cmd_dedup(args):
    records, errs = core.load_json(args.records)
    if errs:
        print(json.dumps({"error": errs}, ensure_ascii=False), file=sys.stderr)
        return 2
    if not isinstance(records, list):
        # Allow {"records": [...]} wrapper.
        if isinstance(records, dict) and isinstance(records.get("records"), list):
            records = records["records"]
        else:
            print(json.dumps({"error": "records file must contain a JSON list"},
                             ensure_ascii=False), file=sys.stderr)
            return 2
    groups = core.dedup_records(records)
    out = {"groups": groups, "group_count": len(groups),
           "record_count": len(records),
           "canonical_keys": [g["canonical_id"] for g in groups]}
    core.write_json(args.out, out)
    print(json.dumps({"groups": len(groups), "records": len(records),
                      "out": args.out}, ensure_ascii=False, indent=2))
    return 0


def cmd_normalize(args):
    try:
        rec = json.loads(args.record)
    except Exception as exc:
        print(json.dumps({"error": "invalid record JSON: %s" % exc},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    ck = core.canonical_key(rec)
    print(json.dumps({
        "canonical_key": ("%s:%s" % ck) if ck else None,
        "kind": ck[0] if ck else None,
        "key": ck[1] if ck else None,
        "type": core.classify_type(rec),
        "version_marker": core.version_marker(rec),
        "doi": core.extract_doi(rec),
        "arxiv": core.extract_arxiv(rec),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_inventory(args):
    case_id, contract, data, errors = core.extract_case(args.input)
    manifest = {
        "case_id": case_id, "contract_fields": contract,
        "case_path": data.get("case_path"), "task_path": data.get("task_path"),
        "materials": data.get("materials"), "errors": errors,
    }
    if args.out:
        core.write_json(args.out, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    args = _parse_args(argv)
    handlers = {
        "scaffold": cmd_scaffold,
        "run": cmd_run,
        "finalize": cmd_finalize,
        "dedup": cmd_dedup,
        "normalize": cmd_normalize,
        "inventory": cmd_inventory,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print('{"error":"interrupted"}', file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
