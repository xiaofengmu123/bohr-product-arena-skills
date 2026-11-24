"""finalize.py - merge contract, inventory and answers into result.json.

Pure standard library. Deterministic (sorted keys, fixed ordering).
Also writes inventory.md and evidence_map.json for human/handoff review.

Usage:
    python3 scripts/finalize.py --input /input --output /app/output --work /app/work
"""
import argparse
import json
import os
import sys
import time
import platform

# lifted out of the response limit is never touched here
UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_iso():
    return time.strftime(UTC_FMT, time.gmtime())


def load_json(path, default):
    """Read a UTF-8 (with optional BOM) JSON file; never raise, return default
    and record the error via caller-supplied sink."""
    if not path or not os.path.isfile(path):
        return default, None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001 - degradation is the point
        return default, "%s: %s" % (type(exc).__name__, exc)


def load_case(input_dir):
    """Load case.json if present; tolerate a directory or a bare file."""
    candidates = []
    if os.path.isdir(input_dir):
        candidates.append(os.path.join(input_dir, "case.json"))
        for child in sorted(os.listdir(input_dir)):
            if child.lower() == "case.json":
                candidates.append(os.path.join(input_dir, child))
    else:
        candidates.append(input_dir)
    case, err = None, None
    for cand in candidates:
        if os.path.isfile(cand):
            case, err = load_json(cand, None)
            if case is not None:
                return case, cand, None
            return {}, cand, err
    return {}, None, "case.json not found under %s" % input_dir


def resolve_case_id(case):
    for key in ("case_id", "id", "challenge_id", "challengeId", "armiId"):
        if isinstance(case, dict) and case.get(key):
            return str(case[key])
    return "unknown"


def find_contract(case):
    """Return the output_contract dict if present under any plausible key."""
    if not isinstance(case, dict):
        return {}
    for key in ("output_contract", "outputContract", "contract",
                "expected_output", "expectedOutput", "outputs", "output"):
        val = case.get(key)
        if isinstance(val, dict):
            return val
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return {"items": val}
    return {}


def contract_default_for(sample):
    """Best-effort typed empty value for an unknown contract key."""
    if isinstance(sample, list):
        return []
    if isinstance(sample, dict):
        return {}
    if isinstance(sample, bool):
        return False
    if isinstance(sample, (int, float)):
        return 0 if isinstance(sample, int) else 0.0
    if isinstance(sample, str):
        return ""
    return None


def build_result(case, inventory, answers, start_iso, meta):
    """Assemble the final result dict. Never fabricates values."""
    case_id = resolve_case_id(case)
    contract = find_contract(case)

    inv = inventory or {}
    ans = answers or {}
    ans_items = ans.get("answers") if isinstance(ans, dict) else None

    # deterministic source list comes from inventory
    sources = inv.get("files", []) if isinstance(inv, dict) else []

    errors = list(inv.get("errors", []) or []) if isinstance(inv, dict) else []
    degradations = list(inv.get("degradations", []) or []) if isinstance(inv, dict) else []
    limitations = list(inv.get("limitations", []) or []) if isinstance(inv, dict) else []
    uncertainty_items = list(inv.get("uncertainty", []) or []) if isinstance(inv, dict) else []

    # merge answer-level uncertainties and limitations
    if isinstance(ans, dict):
        for u in ans.get("uncertainty") or []:
            if u not in uncertainty_items:
                uncertainty_items.append(u)
        for u in ans.get("limitations") or []:
            if u not in limitations:
                limitations.append(u)

    change_log = _build_change_log(inv, ans, start_iso, meta)

    # decide status honestly
    status = _decide_status(case, inv, ans_items, errors, limitations)

    result = {
        "case_id": case_id,
        "status": status,
        "sources": sources,
        "uncertainty": uncertainty_items,
        "limitations": limitations,
        "pending_human": _pending_human(inv, ans, limitations, uncertainty_items),
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "runtime": {
            "start_utc": start_iso,
            "end_utc": now_iso(),
            "python_version": platform.python_version(),
            "platform": platform.system(),
        },
        "library": _build_library(inv),
        "answers": ans_items or [],
        "failures_recovery": list(inv.get("failures_recovery", []) or []) if isinstance(inv, dict) else [],
    }

    # echo every output_contract key verbatim, preserving unknown keys.
    # Standard structural fields keep their computed value unless the agent
    # explicitly supplied an output_map entry for that exact key, so that a
    # contract key named e.g. "library"/"answers" does not wipe real data.
    standard_keys = {
        "case_id", "status", "sources", "uncertainty", "limitations",
        "pending_human", "change_log", "errors", "degradations", "runtime",
        "answers", "library", "failures_recovery",
    }
    output_map = ans.get("output") if isinstance(ans, dict) and isinstance(ans.get("output"), dict) else {}
    for key, sample in contract.items():
        if key in standard_keys and key in result:
            if key in output_map:
                result[key] = output_map[key]
            continue
        result[key] = output_map.get(key, contract_default_for(sample))

    return result


def _build_library(inv):
    """Expose the six-dimension inventory as first-class result fields.

    file types / metadata / directory layers / duplicates / versions /
    permissions+source are all carried so a reviewer can verify coverage
    without reopening /app/work."""
    if not isinstance(inv, dict):
        return {}
    return {
        "summary": inv.get("summary", {}),
        "file_types": inv.get("file_types", {}),
        "directories": inv.get("directories", []),
        "directory_tree": inv.get("directory_tree", {}),
        "duplicate_groups": inv.get("duplicate_groups", []),
        "version_families": inv.get("version_families", {}),
        "empty_files": inv.get("empty_files", []),
    }


def _decide_status(case, inv, ans_items, errors, limitations):
    if case is None or not isinstance(case, dict) or not case:
        return "input_error"
    n_material = 0
    if isinstance(inv, dict):
        summary = inv.get("summary") or {}
        n_material = summary.get("n_material_files")
        if n_material is None:
            n_material = len(inv.get("files", []) or [])
    if n_material == 0:
        return "insufficient_evidence"
    if isinstance(ans_items, list) and ans_items:
        # answers present: ok unless a blocking limitation is flagged
        blocking = any(
            isinstance(x, dict) and str(x.get("blocking", False)).lower() == "true"
            for x in limitations
        )
        return "partial" if (errors or blocking) else "ok"
    return "partial"


def _pending_human(inv, ans, limitations, uncertainty_items):
    out = []
    if isinstance(ans, dict):
        for item in ans.get("pending_human") or []:
            out.append(item)
    for lim in limitations:
        if isinstance(lim, dict) and lim.get("needs_human"):
            out.append({"item": lim.get("limitation", ""), "reason": lim.get("reason", ""),
                        "files": lim.get("files", [])})
    for u in uncertainty_items:
        if isinstance(u, dict) and u.get("level") == "insufficient_evidence":
            out.append({"item": u.get("item", ""), "reason": u.get("reason", ""),
                        "missing": u.get("missing", None)})
    # dedupe by JSON fingerprint, keep stable order
    seen, unique = set(), []
    for item in out:
        fp = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if fp not in seen:
            seen.add(fp)
            unique.append(item)
    return unique


def _build_change_log(inv, ans, start_iso, meta):
    log = []
    skel = meta.get("skeleton") if isinstance(meta, dict) else None
    if skel:
        log.append({
            "step": "skeleton",
            "start_utc": skel.get("start_utc", start_iso),
            "end_utc": skel.get("start_utc", start_iso),
            "action": "write partial result skeleton (contract keys echo with typed empties)",
            "status": "partial",
            "detail": "recovered from %s" % skel.get("path", "work/.skeleton_meta.json"),
        })
    if isinstance(inv, dict):
        for entry in inv.get("change_log", []) or []:
            log.append(entry)
    if isinstance(ans, dict):
        for entry in ans.get("change_log", []) or []:
            log.append(entry)
    log.append({
        "step": "finalize",
        "start_utc": start_iso,
        "end_utc": now_iso(),
        "action": meta.get("action", "merge contract+inventory+answers"),
        "status": meta.get("status", "ok"),
        "detail": meta.get("detail", ""),
    })
    return log


def write_outputs(result, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    if work_dir:
        os.makedirs(work_dir, exist_ok=True)

    result_path = os.path.join(output_dir, "result.json")
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

    _write_inventory_md(result, output_dir)
    _write_evidence_map(result, output_dir)
    return result_path


def _write_inventory_md(result, output_dir):
    lines = ["# 科研知识库资料清单（自动生成）", ""]
    lines.append("- case_id: %s" % result.get("case_id"))
    lines.append("- status: %s" % result.get("status"))
    summary = result.get("library") or {}
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    for key in sorted(summary):
        lines.append("- %s: %s" % (key, json.dumps(summary[key], ensure_ascii=False)))
    lines.append("")
    lines.append("## 文件清单")
    lines.append("")
    lines.append("| 相对路径 | SHA256(前16) | 类型 | 大小 | 编码 | 可读 | 版本标记 |")
    lines.append("|---|---|---|---|---|---|---|")
    for src in result.get("sources") or []:
        sha = (src.get("sha256") or "")[:16]
        perms = src.get("permissions") or {}
        markers = ",".join(src.get("version") or [])
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            src.get("path", ""), sha, src.get("type", ""), src.get("size", ""),
            src.get("text_encoding") or "", perms.get("readable", ""), markers))
    lines.append("")
    lines.append("## 不确定性")
    lines.append("")
    for u in result.get("uncertainty") or []:
        lines.append("- [%s] %s" % (u.get("level"), u.get("item")))
        if u.get("reason"):
            lines.append("  - 原因: %s" % u["reason"])
        if u.get("missing"):
            lines.append("  - 缺什么: %s" % json.dumps(u["missing"], ensure_ascii=False))
    lines.append("")
    lines.append("## limitations（显式缺口）")
    lines.append("")
    for lim in result.get("limitations") or []:
        lines.append("- %s" % lim.get("limitation"))
        if lim.get("impact"):
            lines.append("  - 影响: %s" % lim["impact"])
        if lim.get("files"):
            lines.append("  - 涉及: %s" % ", ".join(lim["files"]))
    lines.append("")
    path = os.path.join(output_dir, "inventory.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _write_evidence_map(result, output_dir):
    out = {"case_id": result.get("case_id"), "answers": []}
    for idx, item in enumerate(result.get("answers") or []):
        out["answers"].append({
            "index": idx,
            "question": item.get("question"),
            "uncertainty": item.get("uncertainty"),
            "missing": item.get("missing"),
            "evidence": item.get("evidence") or [],
        })
    path = os.path.join(output_dir, "evidence_map.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def write_skeleton(case, output_dir, work_dir):
    """Minimal legal partial result written BEFORE heavy inventory work."""
    os.makedirs(output_dir, exist_ok=True)
    case_id = resolve_case_id(case)
    contract = find_contract(case)
    result = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "change_log": [{
            "step": "skeleton",
            "start_utc": now_iso(),
            "end_utc": now_iso(),
            "action": "write partial result skeleton (contract keys echo with typed empties)",
            "status": "partial",
            "detail": "inventory and answers not yet available",
        }],
        "errors": [],
        "degradations": [],
        "runtime": {"start_utc": now_iso()},
        "library": {},
        "answers": [],
        "failures_recovery": [],
    }
    for key, sample in contract.items():
        result[key] = contract_default_for(sample)
    path = os.path.join(output_dir, "result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def run_finalize(input_dir, output_dir, work_dir, start_iso=None, meta=None):
    start_iso = start_iso or now_iso()
    meta = meta or {}
    case, case_path, case_err = load_case(input_dir)
    inv, _ = load_json(os.path.join(work_dir, "inventory.json"), {})
    ans, _ = load_json(os.path.join(work_dir, "answers.json"), {})
    skel, _ = load_json(os.path.join(work_dir, ".skeleton_meta.json"), None)

    meta = dict(meta)
    if skel:
        meta["skeleton"] = skel
    if case_err:
        meta.setdefault("detail", "")

    result = build_result(case, inv, ans, start_iso, meta)
    if case_err and case_err not in result.get("errors", []):
        result.setdefault("errors", []).append("case.json: %s" % case_err)
    path = write_outputs(result, output_dir, work_dir)
    return path, result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Assemble final result.json for the research KB QA skill.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)
    path, result = run_finalize(args.input, args.output, args.work,
                                meta={"action": "finalize via CLI"})
    print("wrote %s (status=%s)" % (path, result.get("status")))
    return 0


if __name__ == "__main__":
    sys.exit(main())