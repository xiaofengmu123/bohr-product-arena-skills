"""selftest.py — self-check for the document-library repair Skill.

Runs the pipeline twice on a synthetic, deterministic input tree and verifies:

1. Both runs produce byte-identical outputs after normalizing wall-clock
   timestamps (determinism guarantee).
2. result.json is valid JSON with non-empty case_id, a valid status, and every
   output_contract field back-filled.
3. All editable artifacts exist and are non-empty where applicable.
4. Degraded inputs (empty dir, bad JSON) do not crash the pipeline.

Usage (from the Skill package root):

    python3 -B scripts/selftest.py

Exit code 0 = all checks passed; 1 = at least one check failed.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402  (stdlib-only sibling module)

EXPECTED_ARTIFACTS = [
    "library_manifest.csv",
    "directory_tree.txt",
    "dedup_version.json",
    "recovery_report.json",
    "library_index.json",
    "library_manifest.md",
    "operation_log.jsonl",
]


def build_synthetic_input(root):
    """Create a deterministic, self-contained case for the pipeline."""
    case_json = os.path.join(root, "case.json")
    task_md = os.path.join(root, "task.md")
    with open(task_md, "w", encoding="utf-8") as fh:
        fh.write("# task\nchallenge_id: `challenge-selftest`\n")
    with open(case_json, "w", encoding="utf-8") as fh:
        json.dump({
            "case_id": "case-selftest-0001",
            "output_contract": {
                "library": {"type": "array"},
                "summary": {"type": "object"},
                "resolution": {"type": "string"},
            },
        }, fh, ensure_ascii=False)

    # duplicate pair (identical content, different names)
    for name in ("dup_a.txt", "dup_b.txt"):
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write("same content\nsecond line\n")

    # version candidates (normalized stem collapses to "report")
    with open(os.path.join(root, "report_v1.txt"), "w", encoding="utf-8") as fh:
        fh.write("alpha\nbeta\n")
    with open(os.path.join(root, "report_v2.txt"), "w", encoding="utf-8") as fh:
        fh.write("alpha\nbeta\ngamma\n")

    # a catalog (JSON) whose records partially match the files
    with open(os.path.join(root, "catalog.json"), "w", encoding="utf-8") as fh:
        json.dump([
            {"filename": "dup_a.txt", "title": "Duplicate A"},
            {"filename": "report_v2.txt", "title": "Report"},
            {"filename": "ghost.txt", "title": "Missing"},
        ], fh, ensure_ascii=False)

    # bad JSON (must be recorded, not crash)
    with open(os.path.join(root, "bad.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")

    # empty directory
    os.makedirs(os.path.join(root, "empty_dir"), exist_ok=True)

    # hard link (alias) — tolerated if the platform disallows it
    try:
        os.link(os.path.join(root, "dup_a.txt"), os.path.join(root, "dup_a_alias.txt"))
    except OSError:
        pass

    return case_json


def run_once(input_dir, output_dir):
    rc = core_impl.main([
        "--input", input_dir,
        "--output", output_dir,
        "--work", os.path.join(output_dir, "..", "work"),
        "--case-json", os.path.join(input_dir, "case.json"),
        "--task-path", os.path.join(input_dir, "task.md"),
    ])
    return rc


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize_result(obj):
    obj = dict(obj)
    obj.pop("runtime", None)
    change_log = []
    for entry in obj.get("change_log", []) or []:
        entry = dict(entry)
        entry.pop("time", None)
        change_log.append(entry)
    obj["change_log"] = change_log
    return obj


def normalize_log_lines(path):
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            obj.pop("time", None)
            out.append(obj)
    return out


def main():
    checks = []
    tmp = tempfile.mkdtemp(prefix="librepair-selftest-")

    def record(name, ok, detail=""):
        checks.append((name, ok, detail))
        print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))

    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp, exist_ok=True)
        build_synthetic_input(inp)

        out1 = os.path.join(tmp, "out1")
        out2 = os.path.join(tmp, "out2")
        rc1 = run_once(inp, out1)
        rc2 = run_once(inp, out2)
        record("pipeline exit 0 (run 1)", rc1 == 0, "rc=%d" % rc1)
        record("pipeline exit 0 (run 2)", rc2 == 0, "rc=%d" % rc2)

        # determinism
        r1 = load_json(os.path.join(out1, "result.json"))
        r2 = load_json(os.path.join(out2, "result.json"))
        same = json.dumps(normalize_result(r1), sort_keys=True) == \
            json.dumps(normalize_result(r2), sort_keys=True)
        record("result.json deterministic", same)

        log1 = normalize_log_lines(os.path.join(out1, "operation_log.jsonl"))
        log2 = normalize_log_lines(os.path.join(out2, "operation_log.jsonl"))
        record("operation_log.jsonl deterministic", log1 == log2)

        # artifacts exist
        missing = [a for a in EXPECTED_ARTIFACTS if not os.path.isfile(os.path.join(out1, a))]
        record("all artifacts present", not missing, "missing=%s" % missing)

        # contract back-fill
        contract_fields = ["library", "summary", "resolution"]
        present = all(f in r1 for f in contract_fields)
        record("contract fields back-filled", present)

        # status / case_id
        record("case_id non-empty", bool(r1.get("case_id")))
        record("status valid", r1.get("status") in core_impl.ALLOWED_STATUS,
               "status=%s" % r1.get("status"))

        # semantic spot-checks
        dup = r1.get("computed", {}).get("duplicate_groups", [])
        dup_members = set()
        for g in dup:
            dup_members.update(g.get("members", []))
        record("duplicate group detected", "dup_a.txt" in dup_members
               and "dup_b.txt" in dup_members, "groups=%d" % len(dup))
        ver = r1.get("computed", {}).get("version_candidates", [])
        record("version candidate detected", len(ver) >= 1)
        ver_diff = None
        for g in ver:
            for m in g.get("members", []):
                if m.get("diff") is not None:
                    ver_diff = m["diff"]
        record("version diff computed", ver_diff is not None
               and ver_diff.get("lines_added") == 1, "diff=%r" % (ver_diff,))
        rec = r1.get("computed", {}).get("reconciliation", {})
        record("orphaned file recorded", "dup_a_alias.txt" in rec.get("orphaned", [])
               or "dup_a.txt" in rec.get("orphaned", []))
        record("bad JSON recorded", any(
            e.get("kind") == "bad_json" for e in r1.get("errors", [])))

        # --check passes
        rc_check = core_impl.main(["--check", os.path.join(out1, "result.json"),
                                   "--case-json", os.path.join(inp, "case.json")])
        record("--check passes", rc_check == 0, "rc=%d" % rc_check)

        # degraded inputs do not crash
        empty_dir = os.path.join(tmp, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        with open(os.path.join(empty_dir, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "case-empty", "output_contract": {}}, fh)
        rc_empty = core_impl.main([
            "--input", empty_dir, "--output", os.path.join(tmp, "oute"),
            "--case-json", os.path.join(empty_dir, "case.json"),
            "--task-path", os.path.join(empty_dir, "task.md"),
        ])
        rempty = load_json(os.path.join(tmp, "oute", "result.json"))
        record("empty input -> insufficient_evidence", rc_empty == 0
               and rempty.get("status") == "insufficient_evidence",
               "status=%s" % rempty.get("status"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in checks if not c[1]]
    print("\n%d checks, %d failed" % (len(checks), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
