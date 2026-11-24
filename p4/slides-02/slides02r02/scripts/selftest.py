#!/usr/bin/env python3
"""selftest.py — self-check, renderability probe, finalize, and package selftest.

Subcommands (all stdlib-only, location-independent):

    check [--status]      audit /app/output/result.json, write self_check.json,
                          exit 1 if any REQUIRED check fails
    pptx <file>           renderability proxy for an editable .pptx (zip/slide
                          count/placeholder scan/overflow heuristic); no Office
                          renderer needed
    finalize --status S   set final status, run check, embed self_check summary
    selftest              end-to-end smoke test of scaffold/record/check against
                          a temp demo input; proves the package actually runs

'check' distinguishes REQUIRED checks (exit-code-relevant) from informational
ones.  A partial result is allowed to have null/empty contract fields; a
'complete' result must have delivered files and non-empty maps.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

VALID_STATUS = {"partial", "complete", "failed", "insufficient_evidence", "input_error", "ok"}
RESERVED = {"case_id", "status", "sources", "uncertainty", "pending_human",
            "change_log", "runtime", "errors", "degradations", "self_check"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def load_json_text(path: Path):
    errors = []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, [f"read failed: {exc}"]
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None, ["undecodable bytes"]
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        return None, [f"bad json: {exc}"]


def write_json_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def build_checks(result: dict, output_dir: Path):
    """Return list of check dicts.  REQUIRED flags drive the exit code."""
    checks = []
    cid = result.get("case_id")
    status = result.get("status")

    checks.append({"id": "case_id", "required": True,
                   "pass": isinstance(cid, str) and bool(cid) and cid != "unknown",
                   "detail": f"case_id={cid!r}"})
    checks.append({"id": "status", "required": True,
                   "pass": status in VALID_STATUS, "detail": f"status={status!r}"})

    cl = result.get("change_log")
    ok_cl = isinstance(cl, list) and bool(cl)
    checks.append({"id": "change_log_present", "required": True,
                   "pass": ok_cl, "detail": f"{len(cl) if isinstance(cl, list) else 0} entries"})
    sorted_ok = True
    if isinstance(cl, list) and len(cl) >= 2:
        ts = [x.get("t") for x in cl if isinstance(x, dict) and x.get("t")]
        sorted_ok = ts == sorted(ts)
    checks.append({"id": "change_log_sorted", "required": True,
                   "pass": sorted_ok, "detail": "time-sorted ascending"})

    checks.append({"id": "sources_type", "required": True,
                   "pass": isinstance(result.get("sources"), list),
                   "detail": "sources is list"})
    checks.append({"id": "uncertainty_type", "required": True,
                   "pass": isinstance(result.get("uncertainty"), dict),
                   "detail": "uncertainty is object"})
    checks.append({"id": "pending_human_type", "required": True,
                   "pass": isinstance(result.get("pending_human"), list),
                   "detail": "pending_human is list"})

    contract_keys = [k for k in result.keys() if k not in RESERVED]
    checks.append({"id": "contract_fields_present", "required": True,
                   "pass": len(contract_keys) > 0,
                   "detail": f"{len(contract_keys)} contract keys"})

    rt = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
    checks.append({"id": "early_scaffold_marker", "required": True,
                   "pass": bool(rt.get("scaffold_t")),
                   "detail": "runtime.scaffold_t recorded (early落盘 audit)"})

    pf = result.get("presentation_file")
    pf_path = None
    if isinstance(pf, str) and pf:
        pf_path = Path(pf) if Path(pf).is_absolute() else output_dir / pf
    checks.append({"id": "presentation_file_delivered", "required": status == "complete",
                   "pass": bool(pf_path and pf_path.exists()),
                   "detail": f"presentation_file={pf!r}"})

    for key, label in (("page_plan", "page_plan_nonempty"),
                       ("evidence_map", "evidence_map_nonempty")):
        val = result.get(key)
        nonempty = bool(val) if isinstance(val, (list, dict)) else val is not None
        checks.append({"id": label, "required": status == "complete",
                       "pass": nonempty, "detail": f"{key} populated"})

    pv = result.get("preview_files")
    checks.append({"id": "preview_files_nonempty", "required": status == "complete",
                   "pass": bool(isinstance(pv, list) and pv),
                   "detail": "preview_files populated"})

    return checks


def cmd_check(output_dir: Path) -> int:
    path = output_dir / "result.json"
    result, errs = load_json_text(path)
    if result is None or not isinstance(result, dict):
        result = {}
        checks = [{"id": "result_json", "required": True, "pass": False,
                   "detail": "result.json missing or invalid JSON"}]
    else:
        checks = build_checks(result, output_dir)
        checks.insert(0, {"id": "result_json", "required": True, "pass": True,
                          "detail": "valid JSON object"})
    required_fail = [c for c in checks if c["required"] and not c["pass"]]
    self_check = {
        "t": now_iso(),
        "status": result.get("status"),
        "pass_all": not required_fail,
        "required_failed": [c["id"] for c in required_fail],
        "checks": checks,
    }
    write_json_atomic(output_dir / "self_check.json", self_check)
    for c in checks:
        mark = "OK " if c["pass"] else "FAIL"
        tag = "REQ" if c["required"] else "info"
        print(f"[{mark}][{tag}] {c['id']}: {c['detail']}")
    return 1 if required_fail else 0


PLACEHOLDER_RE = re.compile(r"TODO|TBD|Lorem|FIXME|XXX|\{\{|\}\}", re.IGNORECASE)
SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml$")
TEXT_RE = re.compile(r"<a:t[^>]*>(.*?)</a:t>", re.DOTALL)


def _clean_text(xml_text: str) -> str:
    # unescape common XML entities, drop tags if any remain
    t = xml_text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    t = re.sub(r"<[^>]+>", "", t)
    return t


def cmd_pptx(args) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"pptx missing: {path}")
        return 1
    try:
        if not zipfile.is_zipfile(path):
            print("not a valid .pptx (no zip header)")
            return 1
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            slides = sorted(
                (n for n in names if SLIDE_RE.match(n)),
                key=lambda n: int(SLIDE_RE.match(n).group(1)),
            )
            overflow = []
            placeholders = []
            total_chars = 0
            for n in slides:
                xml_text = z.read(n).decode("utf-8", errors="replace")
                texts = [_clean_text(m) for m in TEXT_RE.findall(xml_text)]
                chars = sum(len(t) for t in texts)
                total_chars += chars
                joined = "\n".join(texts)
                if chars > 800:
                    overflow.append({"slide": SLIDE_RE.match(n).group(1), "chars": chars})
                for ph in set(PLACEHOLDER_RE.findall(joined)):
                    placeholders.append({"slide": SLIDE_RE.match(n).group(1), "token": ph})
        summary = {
            "file": str(path),
            "slides": len(slides),
            "total_chars": total_chars,
            "overflow_heuristic": overflow,
            "placeholder_hits": placeholders,
        }
        print(json.dumps(summary, ensure_ascii=False))
        # exit 1 only on hard failure (bad zip); overflow is a warning, not fatal
        return 0
    except Exception as exc:  # noqa: BLE001 - report, do not crash the agent
        print(f"pptx check failed: {exc}")
        return 1


def cmd_finalize(args, output_dir: Path) -> int:
    path = output_dir / "result.json"
    result, _ = load_json_text(path)
    if result is None or not isinstance(result, dict):
        result = {"case_id": "unknown", "status": args.status,
                  "sources": [], "uncertainty": {}, "pending_human": [],
                  "change_log": [], "errors": []}
    result["status"] = args.status
    result.setdefault("change_log", []).append({
        "t": now_iso(), "step": "finalize", "action": f"set_status={args.status}",
        "status": "ok", "reason": args.note or "finalize",
    })
    write_json_atomic(output_dir / "result.json", result)
    code = cmd_check(output_dir)
    sc = load_json_text(output_dir / "self_check.json")[0]
    result["self_check"] = sc
    write_json_atomic(output_dir / "result.json", result)
    print(f"finalize status={args.status} check_pass_all={sc.get('pass_all')}")
    return code


def cmd_selftest(here: Path) -> int:
    root = Path(tempfile.mkdtemp(prefix="slide-skill-selftest-"))
    try:
        inp = root / "input"
        wrk = root / "work"
        out = root / "output"
        for d in (inp, wrk, out):
            d.mkdir(parents=True)
        (inp / "case.json").write_text(json.dumps({
            "case_id": "demo-001",
            "output_contract": [
                "presentation_file", "preview_files", "speaker_notes",
                "page_plan", "evidence_map", "version_records",
            ],
        }), encoding="utf-8")
        (inp / "task.md").write_text("# demo task\n", encoding="utf-8")

        run_env = dict(os.environ, INPUT_DIR=str(inp), WORK_DIR=str(wrk), OUTPUT_DIR=str(out))
        scaffold_py = here / "scaffold.py"
        record_py = here / "record.py"
        self_py = here / "selftest.py"

        steps = [
            ([scaffold_py], "scaffold"),
            ([record_py, "change", "--step", "inventory", "--action", "list files",
              "--reason", "selftest"], "record change"),
            ([record_py, "source", "--ref", "Fig 1", "--loc", "paper p.3",
              "--kind", "figure", "--quote", "Fig. 1 shows the trend."],
             "record source"),
            ([record_py, "evidence", "--page", "1", "--role", "result",
              "--claim", "demo claim", "--refs", "Fig 1", "--loc", "p.3"],
             "record evidence"),
            ([record_py, "contract", "--field", "presentation_file",
              "--value", "deck.pptx"], "record contract"),
            ([self_py, "check"], "check"),
        ]
        for argv, label in steps:
            cp = subprocess.run(
                [sys.executable, "-B"] + [str(a) for a in argv],
                env=run_env, capture_output=True, text=True,
            )
            print(f"[{label}] exit={cp.returncode} "
                  f"{'OK' if cp.returncode == 0 else 'FAIL'}")
            if cp.returncode != 0 and label != "check":
                print(cp.stdout)
                print(cp.stderr)
                return 1

        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert result["case_id"] == "demo-001", result.get("case_id")
        assert result["status"] in ("partial", "insufficient_evidence"), result.get("status")
        assert result["presentation_file"] == "deck.pptx"
        assert len(result["sources"]) == 1 and len(result["evidence_map"]) == 1
        sc = json.loads((out / "self_check.json").read_text(encoding="utf-8"))
        print("selftest OK:", json.dumps({
            "case_id": result["case_id"],
            "status": result["status"],
            "self_check_pass_all": sc.get("pass_all"),
        }, ensure_ascii=False))
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", default=env("OUTPUT_DIR", "/app/output"))
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--output", dest="output", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", parents=[parent])
    p.set_defaults(func=lambda a: cmd_check(Path(a.output)))
    p = sub.add_parser("pptx")
    p.add_argument("file")
    p.set_defaults(func=lambda a: cmd_pptx(a))
    p = sub.add_parser("finalize", parents=[parent])
    p.add_argument("--status", default="partial")
    p.add_argument("--note", default="")
    p.set_defaults(func=lambda a: cmd_finalize(a, Path(a.output)))
    sub.add_parser("selftest").set_defaults(func=lambda a: cmd_selftest(here))

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
