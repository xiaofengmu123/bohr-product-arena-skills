#!/usr/bin/env python3
"""Bootstrap a valid partial result.json skeleton before analysis begins."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C


def main():
    ap = argparse.ArgumentParser(description="Bootstrap result.json skeleton.")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--out", default="/app/output")
    args = ap.parse_args()

    paths = C.resolve_paths(args.case, args.task, None, None, args.out,
                            os.path.join(args.out, "result.json"))
    C.ensure_dirs(paths)

    case_data = C.read_json(paths["case"])
    skeleton = C.default_skeleton(case_data)
    # Reflect all output_contract keys at top level as well.
    C.fill_output_contract_fields(skeleton, case_data)

    result_path = paths["result"]
    existing = C.load_existing_result(result_path)
    if existing:
        # Do not clobber an already-dropped deliverable; keep it and re-mark.
        skeleton = existing
        skeleton.setdefault("status", "in_progress")
        C.fill_output_contract_fields(skeleton, case_data)
        C.write_json(result_path, skeleton)
    else:
        C.write_json(result_path, skeleton)

    print(C.utcnow(), "bootstrap ok ->", result_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
