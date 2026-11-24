#!/usr/bin/env python3
"""evidence-grounded-writing helpers: scaffold / extract / finalize / check.

Standard library only. Deterministic where possible; never fabricates data.
Each subcommand writes partial deliverables early and tolerates missing inputs.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

STATUS_SET = {"complete", "partial", "failed", "in_progress"}
DEFAULT_CONTRACT = ["deliverable", "sources", "change_log"]
CONTRACT_KEYS = ("output_contract", "outputContract", "contract", "result_schema")
MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2MB per text file


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    raw = read_bytes(path)
    if len(raw) > max_bytes:
        return None, "oversize:%d bytes" % len(raw)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace"), "decode-replace"


def read_json(path):
    try:
        txt, err = read_text(path)
    except FileNotFoundError:
        return None, "missing:" + path
    if err:
        return None, err
    try:
        return json.loads(txt), None
    except (json.JSONDecodeError, ValueError) as e:
        return None, "bad-json:%s" % e


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def get_case_id(case):
    if not isinstance(case, dict):
        return None
    for k in ("case_id", "caseId", "id"):
        v = case.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def echo_contract(case):
    """Return (fields:dict, source_key). Preserves key names and order."""
    if not isinstance(case, dict):
        return {}, None
    for k in CONTRACT_KEYS:
        v = case.get(k)
        if isinstance(v, dict) and v:
            return dict(v), k
    return {}, None


def discover_materials(input_dir):
    items = []
    if not os.path.isdir(input_dir):
        return items
    skip = {"case.json", "task.md"}
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            if name in skip:
                continue
            p = os.path.join(root, name)
            if os.path.isfile(p):
                rel = os.path.relpath(p, input_dir)
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                items.append({"name": rel, "path": p, "kind": ext,
                              "size": os.path.getsize(p)})
    return items


def pdf_to_text(pdf_path, out_path):
    """Try pdftotext -layout; return (ok:bool, message)."""
    for cmd in ("pdftotext",):
        try:
            r = subprocess.run([cmd, "-layout", pdf_path, out_path],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
            if r.returncode == 0 and os.path.exists(out_path):
                return True, ""
            return False, "pdftotext rc=%d: %s" % (r.returncode, r.stderr.strip()[:200])
        except FileNotFoundError:
            return False, "pdftotext not installed"
        except Exception as e:  # noqa: BLE001 - report and degrade
            return False, "pdftotext failed: %s" % e
    return False, "no pdf tool"


def scaffold(case_path, input_dir, out_dir, work_dir, force=False):
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    result_path = os.path.join(out_dir, "result.json")

    # Preserve an already-improved result unless forced.
    if os.path.exists(result_path):
        existing, e2 = read_json(result_path)
        if e2 is None and isinstance(existing, dict) and existing.get("status") not in (None, "in_progress"):
            if not force:
                print(json.dumps({"ok": True, "skipped": "existing non-in_progress result preserved"},
                                 ensure_ascii=False))
                return 0

    case, err = read_json(case_path)
    case_id = get_case_id(case)
    contract, ck = echo_contract(case)

    base = {}
    for k, v in contract.items():
        base[k] = v if v is not None else None
    for k in DEFAULT_CONTRACT:
        base.setdefault(k, None)

    materials = discover_materials(input_dir)
    base.update({
        "case_id": case_id,
        "status": "in_progress",
        "sources": base.get("sources") if isinstance(base.get("sources"), list) else [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": _now(), "action": "scaffold",
            "detail": "initial skeleton; case_id=%s; contract_key=%s; input_error=%s; materials=%d"
                      % (case_id, ck, err, len(materials)),
        }],
        "limitations": [],
        "degradations": [],
        "errors": [] if not err else [err],
        "runtime": {"started_at": _now()},
    })
    write_json(result_path, base)
    write_json(os.path.join(work_dir, "material_index.json"), {
        "input_dir": input_dir, "materials": materials,
        "scanned_at": _now(),
    })
    print(json.dumps({"ok": True, "case_id": case_id, "contract_key": ck,
                      "materials": len(materials), "input_error": err}, ensure_ascii=False))
    return 0


def extract(input_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    txt_dir = os.path.join(work_dir, "materials")
    os.makedirs(txt_dir, exist_ok=True)
    index = {"materials": [], "degradations": []}
    for item in discover_materials(input_dir):
        rec = dict(item)
        out_name = re.sub(r"[^A-Za-z0-9_.\-]+", "_", item["name"]) + ".txt"
        out_path = os.path.join(txt_dir, out_name)
        ok = False
        if item["kind"] in ("txt", "md", "csv", "json", "tex", "xml", "bib", "yaml", "yml"):
            txt, err = read_text(item["path"])
            if err is None:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(txt)
                rec["chars"] = len(txt)
                ok = True
            else:
                rec["error"] = err
        elif item["kind"] == "pdf":
            ok, msg = pdf_to_text(item["path"], out_path)
            if not ok:
                rec["error"] = msg
                index["degradations"].append({"file": item["name"], "reason": msg})
            else:
                try:
                    rec["chars"] = os.path.getsize(out_path)
                except OSError:
                    rec["chars"] = 0
        else:
            rec["error"] = "unsupported kind"
        rec["extracted"] = ok
        rec["text_file"] = os.path.relpath(out_path, work_dir) if ok else None
        index["materials"].append(rec)
    write_json(os.path.join(work_dir, "extract_index.json"), index)
    n_ok = sum(1 for m in index["materials"] if m.get("extracted"))
    print(json.dumps({"ok": True, "extracted": n_ok, "total": len(index["materials"]),
                      "degradations": len(index["degradations"])}, ensure_ascii=False))
    return 0


def _merge_analysis(result, work_dir):
    """Copy empty deliverable/sources fields from work/analysis.json if present."""
    a_path = os.path.join(work_dir, "analysis.json")
    if not os.path.exists(a_path):
        return False
    a, err = read_json(a_path)
    if err or not isinstance(a, dict):
        return False
    changed = False
    if isinstance(a.get("deliverable"), dict):
        cur = result.get("deliverable")
        if not isinstance(cur, dict) or not cur.get("claims"):
            result["deliverable"] = a["deliverable"]
            changed = True
    if isinstance(a.get("sources"), list):
        if not result.get("sources"):
            result["sources"] = a["sources"]
            changed = True
    if isinstance(a.get("uncertainty"), list):
        result.setdefault("uncertainty", [])
        result["uncertainty"] = result["uncertainty"] + a["uncertainty"]
        changed = True
    return changed


def _normalize_status(result):
    st = result.get("status")
    if st not in STATUS_SET:
        result["status"] = "partial"
        return
    deliv = result.get("deliverable")
    has_deliv = isinstance(deliv, dict) and (deliv.get("claims") or deliv.get("manuscript"))
    if st == "complete" and not has_deliv:
        result["status"] = "partial"  # honesty: no complete without deliverable
    if st == "in_progress":
        result["status"] = "partial" if has_deliv else "partial"


def _render_md(result, out_path):
    lines = []
    lines.append("# result — %s" % (result.get("case_id") or "(no case_id)"))
    lines.append("")
    lines.append("- status: `%s`" % result.get("status"))
    d = result.get("deliverable")
    if isinstance(d, dict):
        lines.append("")
        lines.append("## deliverable")
        for key in ("summary", "claims", "evidence_table", "manuscript"):
            v = d.get(key)
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                lines.append("### %s" % key)
                lines.append("```json")
                lines.append(json.dumps(v, ensure_ascii=False, indent=2))
                lines.append("```")
            else:
                lines.append("### %s" % key)
                lines.append("")
                lines.append(str(v))
    for sec in ("sources", "uncertainty", "pending_human", "limitations", "degradations", "errors"):
        v = result.get(sec)
        if isinstance(v, (dict, list)) and v:
            lines.append("")
            lines.append("## %s" % sec)
            lines.append("```json")
            lines.append(json.dumps(v, ensure_ascii=False, indent=2))
            lines.append("```")
        elif v:
            lines.append("")
            lines.append("## %s" % sec)
            lines.append("")
            lines.append(str(v))
    cl = result.get("change_log")
    if isinstance(cl, list) and cl:
        lines.append("")
        lines.append("## change_log")
        for e in cl:
            if isinstance(e, dict):
                lines.append("- `%s` %s: %s" % (e.get("time", "?"), e.get("action", "?"), e.get("detail", "")))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def finalize(case_path, work_dir, out_dir):
    result_path = os.path.join(out_dir, "result.json")
    if not os.path.exists(result_path):
        print(json.dumps({"ok": False, "error": "result.json missing; run scaffold first"},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    result, err = read_json(result_path)
    if err or not isinstance(result, dict):
        result = {"case_id": None, "status": "failed", "sources": [], "uncertainty": [],
                  "pending_human": [], "change_log": [], "limitations": [],
                  "degradations": [], "errors": [err or "result.json not a dict"]}

    case, cerr = read_json(case_path)
    case_id = get_case_id(case) or result.get("case_id")
    contract, _ = echo_contract(case)

    # Ensure contract keys are present in the final result.
    for k, v in contract.items():
        result.setdefault(k, v if v is not None else None)
    for k in DEFAULT_CONTRACT:
        result.setdefault(k, None)

    merged = _merge_analysis(result, work_dir)
    result.setdefault("case_id", case_id)
    _normalize_status(result)
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("limitations", [])
    result.setdefault("degradations", [])
    result.setdefault("errors", [])
    if cerr and cerr not in result["errors"]:
        result["errors"].append(cerr)

    cl = result.setdefault("change_log", [])
    if not isinstance(cl, list):
        cl = []
        result["change_log"] = cl
    cl.append({"time": _now(), "action": "finalize",
               "detail": "validated fields; merged_analysis=%s; contract_keys=%d"
                         % (merged, len(contract))})

    write_json(result_path, result)
    _render_md(result, os.path.join(out_dir, "result.md"))
    print(json.dumps({"ok": True, "case_id": result.get("case_id"), "status": result.get("status"),
                      "merged_analysis": merged}, ensure_ascii=False))
    return 0


def check(out_dir):
    result_path = os.path.join(out_dir, "result.json")
    problems = []
    if not os.path.exists(result_path):
        return 1, ["result.json missing"]
    r, err = read_json(result_path)
    if err or not isinstance(r, dict):
        return 1, ["result.json invalid: %s" % err]
    if not r.get("case_id"):
        problems.append("case_id empty")
    if r.get("status") not in STATUS_SET:
        problems.append("status invalid: %r" % r.get("status"))
    for k in ("sources", "uncertainty", "pending_human", "change_log"):
        if k not in r:
            problems.append("missing field: %s" % k)
    if r.get("status") == "complete" and not isinstance(r.get("deliverable"), dict):
        problems.append("status=complete but deliverable missing")
    return (1 if problems else 0), problems


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--case", default="/input/case.json")
    common.add_argument("--input", default="/input")
    common.add_argument("--out", default="/app/output")
    common.add_argument("--work", default="/app/work")

    ap = argparse.ArgumentParser(description=__doc__, parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scaffold", parents=[common])
    sp.add_argument("--force", action="store_true")

    sub.add_parser("extract", parents=[common])
    sub.add_parser("finalize", parents=[common])
    sub.add_parser("check", parents=[common])

    a = ap.parse_args(argv)
    if a.cmd == "scaffold":
        return scaffold(a.case, a.input, a.out, a.work, force=a.force)
    if a.cmd == "extract":
        return extract(a.input, a.work)
    if a.cmd == "finalize":
        return finalize(a.case, a.work, a.out)
    if a.cmd == "check":
        code, problems = check(a.out)
        print(json.dumps({"ok": code == 0, "problems": problems}, ensure_ascii=False))
        return code
    return 2


if __name__ == "__main__":
    sys.exit(main())
