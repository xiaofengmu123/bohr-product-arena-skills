# -*- coding: utf-8 -*-
"""Self-test battery for the literature-tracking Skill.

Run from the skill root::

    python3 scripts/selftest.py

Each scenario exercises one robustness requirement from ``references/
checklist.md``.  The script prints PASS/FAIL per scenario and exits non-zero
if any scenario fails.  It only uses the standard library and creates all
fixtures under a temporary directory, so it never touches user data.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core
import main as main_mod


RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    suffix = "  -- %s" % detail if detail and not condition else ""
    print("[%s] %s%s" % (mark, name, suffix))
    return bool(condition)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_case(root, case_obj=None, task_text="# task", extra=None):
    inp = Path(root) / "input"
    out = Path(root) / "output"
    work = Path(root) / "work"
    inp.mkdir(parents=True, exist_ok=True)
    if case_obj is not None:
        (inp / "case.json").write_text(
            json.dumps(case_obj, ensure_ascii=False), encoding="utf-8"
        )
    if task_text is not None:
        (inp / "task.md").write_text(task_text, encoding="utf-8")
    if extra:
        for rel, content in extra.items():
            p = inp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return str(inp), str(out), str(work)


def scenario_empty_input(tmp):
    inp, out, work = write_case(tmp, case_obj=None, task_text=None)
    code, result = core.scaffold(inp, out, work)
    final = read_json(os.path.join(out, "result.json"))
    ok = (
        code == 0
        and final.get("status") == "insufficient_evidence"
        and final.get("case_id") == "unknown-case"
        and isinstance(final.get("errors"), list)
        and len(final["errors"]) > 0
    )
    check("empty input -> insufficient_evidence, exit 0, no crash", ok,
          "status=%s" % final.get("status"))
    return final


def scenario_bad_json(tmp):
    inp, out, work = write_case(
        tmp,
        case_obj=None,
        task_text="# task\nchallenge_id: `challenge-badjson`\n",
        extra={"case.json": b'{"case_id": "x", "broken": \x00\x01\x02'},
    )
    code, result = core.scaffold(inp, out, work)
    final = read_json(os.path.join(out, "result.json"))
    has_error = any("json parse" in e for e in final.get("errors", []))
    ok = code == 0 and final.get("status") == "input_error" and has_error
    check("bad JSON -> input_error + errors recorded, no crash", ok,
          "errors=%s" % final.get("errors"))
    return final


def scenario_oversized(tmp):
    big = b"x" * (core.MAX_FILE_BYTES + 10)
    inp, out, work = write_case(
        tmp,
        case_obj={"case_id": "c-big", "output_contract": {"search_results": []}},
        task_text="# task\nchallenge_id: `c-big`\n",
        extra={"materials/huge.pdf": big},
    )
    code, result = core.scaffold(inp, out, work)
    final = read_json(os.path.join(out, "result.json"))
    has_deg = any("oversized" in d for d in final.get("degradations", []))
    ok = code == 0 and has_deg and final.get("case_id") == "c-big"
    check("oversized file -> skipped + degradation recorded", ok,
          "degradations=%s" % final.get("degradations"))
    return final


def scenario_normal(tmp):
    contract = {
        "search_results": [],
        "ranking": [],
        "evidence": [],
        "status_notes": {},
    }
    inp, out, work = write_case(
        tmp,
        case_obj={"case_id": "c-normal", "output_contract": contract},
        task_text="# task\nchallenge_id: `c-normal`\n",
        extra={"materials/notes.md": "# notes\nsome material\n"},
    )
    findings = {
        "search_results": [
            {
                "title": "A Study of X",
                "doi": "10.1000/XYZ",
                "journal": "J. Example",
                "year": 2026,
                "source": "Crossref response",
                "locator": "materials/notes.md",
            }
        ],
        "ranking": [{"doi": "10.1000/XYZ", "rank": 1, "reason": "topical match"}],
        "evidence": [
            {
                "locator": "materials/notes.md",
                "quote": "some material",
                "claim": "material present",
            }
        ],
        "status_notes": {"empty": None, "conflicts": [], "permission_errors": []},
        "status": "ok",
        "sources": [{"name": "materials/notes.md", "role": "local material"}],
        "uncertainty": [{"item": "window boundary", "detail": "needs confirmation"}],
        "pending_human": [],
    }
    findings_path = os.path.join(work, "findings.json")
    os.makedirs(work, exist_ok=True)
    Path(findings_path).write_text(
        json.dumps(findings, ensure_ascii=False), encoding="utf-8"
    )
    code, result = core.finalize(inp, out, work, findings_path)
    final = read_json(os.path.join(out, "result.json"))
    report = Path(os.path.join(out, "report.md"))
    missing = [k for k in contract if k not in final]
    ok = (
        code == 0
        and final.get("case_id") == "c-normal"
        and final.get("status") == "ok"
        and not missing
        and len(final.get("search_results", [])) == 1
        and final.get("search_results")[0]["doi"] == "10.1000/XYZ"
        and report.exists()
        and "c-normal" in report.read_text(encoding="utf-8")
    )
    check("normal run -> full contract + findings merged + report", ok,
          "missing=%s" % missing)
    return final


def scenario_dedup(tmp):
    records = [
        {"doi": "10.1000/ABC", "title": "The Same Paper", "journal": "J1"},
        {"title": "The Same Paper!", "source": "repost"},
        {"arxiv": "arXiv:2401.00001v2", "title": "Another Work"},
        {"doi": "10.5555/UNRELATED", "title": "Unrelated"},
    ]
    dedup = core.dedup_records(records)
    ok = (
        dedup["records_in"] == 4
        and dedup["duplicates_removed"] == 1
        and len(dedup["clusters"]) + len(dedup["orphans"]) == 3
    )
    check("dedup merges DOI/title duplicates via union-find", ok,
          json.dumps(dedup, ensure_ascii=False))
    return dedup


def scenario_normalize():
    rec = {"doi": "https://doi.org/10.1000/XYZ.", "arxiv": "arXiv:2401.00001v2",
           "title": "  The  Same-Paper!  "}
    ok = (
        core.normalize_doi(rec["doi"]) == "10.1000/xyz"
        and core.normalize_arxiv(rec["arxiv"]) == "2401.00001"
        and core.normalize_title(rec["title"]) == "the same paper"
        and core.canonical_id(rec) == "doi:10.1000/xyz"
    )
    check("normalization: DOI/arXiv/title/canonical-id", ok,
          json.dumps([core.canonical_keys(rec)], ensure_ascii=False))
    return rec


def scenario_idempotency(tmp):
    os.environ["SKILL_NOW"] = "2026-11-24T00:00:00+00:00"
    try:
        inp, out, work = write_case(
            tmp,
            case_obj={"case_id": "c-idem", "output_contract": {"search_results": []}},
            task_text="# task\nchallenge_id: `c-idem`\n",
        )
        core.scaffold(inp, out, work)
        core.finalize(inp, out, work, None)
        first = read_json(os.path.join(out, "result.json"))
        out2 = Path(tmp) / "output2"
        core.scaffold(inp, str(out2), work)
        core.finalize(inp, str(out2), work, None)
        second = read_json(os.path.join(out2, "result.json"))
        ok = first == second
        check("idempotent replay (SKILL_NOW fixed) -> identical result.json", ok, "")
        return first
    finally:
        os.environ.pop("SKILL_NOW", None)


def scenario_arg_defaults():
    parser = main_mod.build_parser()
    ns = parser.parse_args(["scaffold"])
    ok = (
        ns.input == core.DEFAULT_INPUT
        and ns.output == core.DEFAULT_OUTPUT
        and ns.work == core.DEFAULT_WORK
    )
    check("missing params -> argparse defaults wired", ok,
          "input=%s output=%s work=%s" % (ns.input, ns.output, ns.work))
    return ns


def scenario_cli_help():
    try:
        proc = subprocess.run(
            [sys.executable, "-B", os.path.join(os.path.dirname(__file__), "main.py"), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        ok = proc.returncode == 0 and "scaffold" in proc.stdout
        check("python main.py --help -> exit 0 and lists subcommands", ok,
              "rc=%s" % proc.returncode)
        return proc
    except Exception as exc:
        check("python main.py --help -> exit 0 and lists subcommands", False, str(exc))
        return None


def main():
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        scenario_arg_defaults()
        scenario_cli_help()
        scenario_empty_input(Path(tmp) / "empty")
        scenario_bad_json(Path(tmp) / "badjson")
        scenario_oversized(Path(tmp) / "big")
        scenario_normal(Path(tmp) / "normal")
        scenario_dedup(Path(tmp) / "dedup")
        scenario_normalize()
        scenario_idempotency(Path(tmp) / "idem")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print("\n%d/%d scenarios passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("FAILED:", ", ".join(r[0] for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
