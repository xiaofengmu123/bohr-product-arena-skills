#!/usr/bin/env python3
"""Systematic literature review runtime helper (standard library only).

Subcommands
-----------
  discover    print the input inventory as JSON
  skeleton    write an early valid result.json + run_state.json
  merge       deep-merge a payload JSON into result.json
  finalize    assemble result.json, review.md, screening_log.json, evidence_map.json
  selfcheck   validate result.json; report coverage + screening + evidence audits

Common options (defaults match the runtime contract):
  --input  INPUT_DIR    default /input
  --output OUTPUT_DIR   default /app/output
  --work   WORK_DIR     default /app/work
"""

import argparse
import copy
import json
import os
import sys
import time

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

CASE_ID_KEYS = ("case_id", "caseId", "id", "armiId", "challenge_id", "challengeId")

DEFAULT_CONTRACT = {
    "review_question": "",
    "subquestions": [],
    "search_strategy": {"query_blocks": []},
    "inclusion_criteria": [],
    "exclusion_criteria": [],
    "screening_records": [],
    "prisma_flow": {},
    "quality_appraisal": [],
    "synthesis": {"conclusions": [], "conflicts": [], "gaps": []},
    "evidence_map": [],
    "uncertainty": [],
}

TEXT_ENCODINGS = ("utf-8-sig", "gb18030", "latin-1")
MAX_FILE = 2 * 1024 * 1024  # skip larger files when reading text


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def epoch_now():
    return time.time()


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def load_json(path):
    """Return (obj, errors, degradations); obj is None on failure."""
    errs, degs = [], []
    if not path or not os.path.exists(path):
        return None, errs, degs
    if os.path.getsize(path) > MAX_FILE:
        degs.append("file too large to load: %s" % path)
        return None, errs, degs
    for enc in TEXT_ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                return json.load(fh), errs, degs
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        except Exception as exc:  # noqa: BLE001
            errs.append("%s: %s" % (path, exc))
            return None, errs, degs
    errs.append("unreadable JSON (all encodings failed): %s" % path)
    return None, errs, degs


def read_text(path):
    """Return (text, errors, degradations) with encoding fallback."""
    errs, degs = [], []
    if not path or not os.path.exists(path):
        return "", errs, degs
    if os.path.getsize(path) > MAX_FILE:
        degs.append("file too large, skipped: %s" % path)
        return "", errs, degs
    for enc in TEXT_ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), errs, degs
        except UnicodeDecodeError:
            continue
        except Exception as exc:  # noqa: BLE001
            errs.append("%s: %s" % (path, exc))
            return "", errs, degs
    errs.append("unreadable text: %s" % path)
    return "", errs, degs


def write_json(path, obj):
    errs = []
    try:
        ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        errs.append("write %s: %s" % (path, exc))
    return errs


def write_text(path, text):
    errs = []
    try:
        ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except Exception as exc:  # noqa: BLE001
        errs.append("write %s: %s" % (path, exc))
    return errs


def read_jsonl(path):
    """Return (list_of_dicts, errors); bad lines are skipped and reported."""
    out, errs = [], []
    if not path or not os.path.exists(path):
        return out, errs
    text, terr, _ = read_text(path)
    errs.extend(terr)
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
            else:
                errs.append("%s:%d not a JSON object" % (path, i))
        except json.JSONDecodeError as exc:
            errs.append("%s:%d %s" % (path, i, exc))
    return out, errs


def discover_input(input_dir):
    inv = {"exists": False, "input_dir": input_dir, "total_files": 0,
           "case_json": None, "task_md": None, "materials": [], "files": []}
    if not input_dir or not os.path.isdir(input_dir):
        return inv
    inv["exists"] = True
    material_ext = (".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml",
                    ".html", ".tex", ".bib", ".ris", ".enw", ".png", ".jpg",
                    ".jpeg", ".docx", ".doc", ".pptx", ".xlsx", ".svg")
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__"))
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir)
            rec = {"path": rel, "abs": full,
                   "size": os.path.getsize(full) if os.path.isfile(full) else 0}
            inv["files"].append(rec)
            inv["total_files"] += 1
            base = name.lower()
            if base == "case.json" and inv["case_json"] is None:
                inv["case_json"] = full
            elif base == "task.md" and inv["task_md"] is None:
                inv["task_md"] = full
            elif base.endswith(material_ext):
                inv["materials"].append(rec)
    return inv


def extract_case_id(obj):
    if not isinstance(obj, dict):
        return ""
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return str(val)
    return ""


def _empty_like(value):
    if isinstance(value, dict):
        return {k: _empty_like(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return None
    return ""


def build_contract(case_obj):
    contract = copy.deepcopy(DEFAULT_CONTRACT)
    if isinstance(case_obj, dict):
        oc = case_obj.get("output_contract") or case_obj.get("outputContract") or {}
        if isinstance(oc, dict):
            for key, val in oc.items():
                if key not in contract:
                    contract[key] = _empty_like(val)
    return contract


def new_skeleton(case_id, contract, note=""):
    result = {
        "case_id": case_id or "unknown",
        "status": STATUS_PARTIAL,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"ts": iso_now(), "action": "skeleton", "detail": note}],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime_seconds": 0.0,
    }
    for key, val in contract.items():
        result[key] = copy.deepcopy(val)
    return result


def deep_merge(base, patch):
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            base[key] = deep_merge(base[key], val)
        else:
            base[key] = copy.deepcopy(val)
    return base


def load_result(args):
    result_path = os.path.join(args.output, "result.json")
    result, errs, degs = load_json(result_path)
    state, _, _ = load_json(os.path.join(args.work, "run_state.json"))
    contract = (state or {}).get("contract") or copy.deepcopy(DEFAULT_CONTRACT)
    if not isinstance(result, dict):
        case_id = (state or {}).get("case_id", "")
        result = new_skeleton(case_id, contract, note="created on load")
        result["status"] = STATUS_INSUFFICIENT
    return result, state if isinstance(state, dict) else {}, contract, errs, degs


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))


def cmd_discover(args):
    print(json.dumps(discover_input(args.input), ensure_ascii=False, indent=2))


def cmd_skeleton(args):
    ensure_dir(args.output)
    ensure_dir(args.work)
    inv = discover_input(args.input)
    case_obj, errs, degs = None, [], []
    if inv.get("case_json"):
        case_obj, errs, degs = load_json(inv["case_json"])
    case_id = extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = build_contract(case_obj if isinstance(case_obj, dict) else None)

    result_path = os.path.join(args.output, "result.json")
    summary = {"case_id": case_id, "wrote": False, "already_exists": False,
               "errors": [], "degradations": []}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, d2 = load_json(result_path)
        if not isinstance(existing, dict):
            result = new_skeleton(case_id, contract, note="repaired unreadable result.json")
            result["errors"].extend(e2)
            result["degradations"].extend(d2)
            summary["errors"].extend(write_json(result_path, result))
            summary["wrote"] = True
    else:
        result = new_skeleton(case_id, contract, note="early skeleton before review begins")
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0:
            result["status"] = STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append("no input files under %s" % args.input)
        elif not inv.get("case_json"):
            result["status"] = STATUS_PARTIAL
            result.setdefault("limitations", []).append(
                "case.json not found; question taken from task.md if readable")
        elif bool(inv.get("case_json")) and not isinstance(case_obj, dict):
            result["status"] = STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append("case.json present but unparseable")
        summary["errors"].extend(write_json(result_path, result))
        summary["wrote"] = True

    state = {"case_id": case_id, "contract": contract, "inventory": inv,
             "started_at": epoch_now(), "input_dir": args.input,
             "output_dir": args.output, "work_dir": args.work}
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    summary["errors"].extend(write_json(os.path.join(args.work, "run_state.json"), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_merge(args):
    payload, errs, degs = load_json(args.payload)
    result_path = os.path.join(args.output, "result.json")
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "errors": errs or ["payload not a JSON object"],
                          "degradations": degs}, ensure_ascii=False, indent=2))
        return
    result, _, _, e2, d2 = load_result(args)
    result = deep_merge(result, payload)
    result.setdefault("change_log", []).append({
        "ts": iso_now(), "action": "merge",
        "detail": "merged " + os.path.basename(args.payload)})
    errs.extend(write_json(result_path, result))
    print(json.dumps({"ok": not errs, "errors": errs, "degradations": degs + d2},
                     ensure_ascii=False, indent=2))


def read_work(args):
    data, errs = {}, []
    for name in ("protocol.json", "synthesis.json"):
        obj, e, _ = load_json(os.path.join(args.work, name))
        errs.extend(e)
        if isinstance(obj, dict):
            data[name] = obj
    for name in ("screening.jsonl", "quality_appraisal.jsonl", "evidence_cards.jsonl"):
        recs, e = read_jsonl(os.path.join(args.work, name))
        errs.extend(e)
        data[name] = recs
    return data, errs


def collect_sources(screening, cards):
    seen, out = [], []
    for rec in list(screening) + list(cards):
        src = rec.get("source") or rec.get("record_id") or ""
        if src and src not in seen:
            seen.append(src)
            out.append(src)
    return out


def compute_prisma(screening, protocol):
    n_dup = sum(1 for r in screening if r.get("duplicate_of"))
    ta = [r for r in screening if r.get("stage") in ("title_abstract", "tiab", "screening")]
    ft = [r for r in screening if r.get("stage") in ("fulltext", "full_text")]
    ta_ex = [r for r in ta if r.get("decision") == "exclude"]
    ft_ex = [r for r in ft if r.get("decision") == "exclude"]
    inc = [r for r in ft if r.get("decision") == "include"]
    identified = None
    search = (protocol or {}).get("search_strategy") or {}
    blocks = search.get("query_blocks") or []
    hits = [b.get("hits") for b in blocks if isinstance(b.get("hits"), (int, float))]
    if hits and len(hits) == len(blocks):
        identified = int(sum(hits))
    elif screening:
        identified = len(ta) + n_dup
    return {"identified": identified, "duplicates_removed": n_dup,
            "title_abstract_screened": len(ta), "title_abstract_excluded": len(ta_ex),
            "fulltext_screened": len(ft), "fulltext_excluded": len(ft_ex),
            "included": len(inc), "recomputed": True}


def write_review_md(args, result, synthesis, screening, cards):
    lines = ["# 系统性文献综述", "", "case_id: %s" % result.get("case_id", "unknown"),
             "status: %s" % result.get("status", ""), ""]
    rq = result.get("review_question") or ""
    if rq:
        lines += ["## 研究问题", "", str(rq), ""]
    for title, key in (("结论", "conclusions"), ("冲突证据", "conflicts"), ("研究空白", "gaps")):
        items = (synthesis or {}).get(key) or []
        if items:
            lines += ["## " + title, ""]
            for it in items:
                lines.append("- " + str(it.get("text") or it.get("statement") or it.get("gap") or ""))
            lines.append("")
    lines += ["## 流程计数", "",
              "- 题录级筛选: %d" % len([r for r in screening if r.get("stage") in ("title_abstract", "tiab", "screening")]),
              "- 全文级筛选: %d" % len([r for r in screening if r.get("stage") in ("fulltext", "full_text")]),
              "- 纳入: %d" % len([r for r in screening if r.get("decision") == "include"]),
              "- 证据卡: %d" % len(cards), ""]
    lines += ["## 证据回链", "",
              "evidence_map.json 每条证据含 locator+quote；screening_log.json 含逐条 decision/reason/criterion_id。", ""]
    write_text(os.path.join(args.output, "review.md"), "\n".join(lines))


def cmd_finalize(args):
    ensure_dir(args.output)
    data, errs = read_work(args)
    result, state, _, e2, _ = load_result(args)
    all_errs = list(errs) + list(e2)

    protocol = data.get("protocol.json") or {}
    synthesis = data.get("synthesis.json") or {}
    screening = data.get("screening.jsonl") or []
    quality = data.get("quality_appraisal.jsonl") or []
    cards = data.get("evidence_cards.jsonl") or []

    for key in ("review_question", "subquestions", "search_strategy",
                "inclusion_criteria", "exclusion_criteria"):
        if key in protocol:
            result[key] = copy.deepcopy(protocol[key])
    result["screening_records"] = screening
    result["quality_appraisal"] = quality
    result["evidence_map"] = cards
    if isinstance(synthesis, dict):
        result["synthesis"] = copy.deepcopy(synthesis)
    result["prisma_flow"] = compute_prisma(screening, protocol)

    sources = collect_sources(screening, cards)
    if sources:
        result["sources"] = sources

    started = (state or {}).get("started_at")
    result["runtime_seconds"] = round(epoch_now() - (started if started else epoch_now()), 1)
    result.setdefault("change_log", []).append({
        "ts": iso_now(), "action": "finalize",
        "detail": "assembled %d screening / %d appraisal / %d evidence records" %
                  (len(screening), len(quality), len(cards))})
    if all_errs:
        merged = list(result.get("errors", []))
        for item in all_errs:
            if item not in merged:
                merged.append(item)
        result["errors"] = merged

    for key in ("sources", "uncertainty", "pending_human", "limitations", "degradations"):
        result.setdefault(key, [])

    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.output, "screening_log.json"), screening)
    write_json(os.path.join(args.output, "evidence_map.json"), cards)
    write_review_md(args, result, synthesis, screening, cards)

    print(json.dumps({"ok": True, "screening": len(screening), "appraisal": len(quality),
                      "evidence": len(cards), "errors": all_errs},
                     ensure_ascii=False, indent=2))


def cmd_selfcheck(args):
    result, _, _, errs, degs = load_result(args)
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("result_json_loadable", isinstance(result, dict), "result.json parses as object")
    add("case_id", bool(result.get("case_id")), str(result.get("case_id")))
    add("status_valid", result.get("status") in STATUSES, str(result.get("status")))

    rq = result.get("review_question")
    add("review_question", bool(rq), "non-empty review_question")
    subs = result.get("subquestions") or []
    add("subquestions", isinstance(subs, list) and len(subs) >= 1, "%d subquestions" % len(subs))

    search = result.get("search_strategy") or {}
    blocks = search.get("query_blocks") or []
    good_blocks = sum(1 for b in blocks
                      if isinstance(b, dict) and b.get("database") and b.get("query") and b.get("intent"))
    add("search_blocks_executable", len(blocks) >= 1 and good_blocks == len(blocks),
        "%d/%d blocks have database+query+intent" % (good_blocks, len(blocks)))

    inc = result.get("inclusion_criteria") or []
    exc = result.get("exclusion_criteria") or []
    add("criteria_present", isinstance(inc, list) and len(inc) >= 1 and isinstance(exc, list) and len(exc) >= 1,
        "in=%d ex=%d" % (len(inc), len(exc)))

    recs = result.get("screening_records") or []
    excl = [r for r in recs if isinstance(r, dict) and r.get("decision") == "exclude"]
    excl_ok = all(r.get("reason") and r.get("criterion_id") for r in excl)
    add("exclude_reasons", excl_ok, "%d excludes with reason+criterion_id" % len(excl))
    add("screening_two_stages", any(r.get("stage") in ("fulltext", "full_text") for r in recs),
        "has fulltext-stage records")

    app_ok = True
    for a in (result.get("quality_appraisal") or []):
        for d in (a.get("dimensions") or []):
            if not (d.get("basis_locator") or d.get("locator")) or not d.get("quote"):
                app_ok = False
    add("appraisal_locator_quote", app_ok, "%d appraisals" % len(result.get("quality_appraisal") or []))

    cards = result.get("evidence_map") or []
    card_ids = set()
    for c in cards:
        if isinstance(c, dict):
            eid = c.get("evidence_id") or c.get("id")
            if eid:
                card_ids.add(eid)
    card_ok = all((c.get("locator") and c.get("quote")) for c in cards if isinstance(c, dict))
    add("evidence_locator_quote", card_ok, "%d evidence cards" % len(cards))

    conclusions = (result.get("synthesis") or {}).get("conclusions") or []
    missing = []
    for c in conclusions:
        for eid in (c.get("evidence_ids") or []):
            if eid not in card_ids:
                missing.append(eid)
    concl_ok = bool(conclusions) and all(c.get("text") and c.get("evidence_ids") for c in conclusions)
    add("conclusions_backlinked", concl_ok and not missing,
        "%d conclusions; missing ids: %s" % (len(conclusions), missing))

    conflicts = (result.get("synthesis") or {}).get("conflicts") or []
    conf_ok = all(c.get("source_a") and c.get("source_b") for c in conflicts)
    add("conflicts_paired", conf_ok, "%d conflicts" % len(conflicts))

    gaps = (result.get("synthesis") or {}).get("gaps") or []
    add("gaps_located", bool(gaps), "%d gaps" % len(gaps))

    passed = sum(1 for c in checks if c["ok"])
    print(json.dumps({"passed": passed, "failed": len(checks) - passed,
                      "status": result.get("status"), "checks": checks,
                      "errors": errs, "degradations": degs},
                     ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    for name, help_text in (("discover", "print input inventory"),
                            ("skeleton", "write early result skeleton"),
                            ("merge", "merge a payload into result.json"),
                            ("finalize", "assemble final artifacts"),
                            ("selfcheck", "validate result.json")):
        p = sub.add_parser(name, help=help_text)
        add_common(p)
    sub.choices["merge"].add_argument("--payload", required=True)
    args = parser.parse_args(argv)
    fn = {"discover": cmd_discover, "skeleton": cmd_skeleton, "merge": cmd_merge,
          "finalize": cmd_finalize, "selfcheck": cmd_selfcheck}.get(args.cmd)
    if fn is None:
        parser.print_help()
        return 1
    fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
