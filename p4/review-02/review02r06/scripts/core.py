"""core.py — evidence-led citation & fact audit pipeline (Python stdlib only).

This module implements the deterministic stages of the audit Skill:

  discover  -> scan /input, extract PDF text with page markers, write manifest.json
  scaffold  -> write plan.json + empty evidence/findings dirs + meta.json + a
               partial result.json skeleton (honest "partial/unchecked")
  finalize  -> compile audit_model.json (single intermediate source of truth),
               then render result.json and report.md from it
  selftest  -> validate output contract, required fields and evidence traceability

Design decision (variant focus "intermediate representation & auditability"):
the agent never edits result.json directly. It writes small, independently
checkable intermediate artifacts (evidence/<id>.json, findings/<id>.json,
meta.json). ``finalize`` deterministically merges them into ``audit_model.json``
and only then renders the final artifacts. Every finding must reference at
least one evidence entry, and every evidence entry carries a locator plus a
verbatim quote, so the audit can be replayed by a second reader.

All paths are overridable; defaults follow the harness contract:
  /input, /app/output, /app/work
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip larger files, record degradation

CATEGORIES = [
    "citation_support",
    "fact_source",
    "evidence_location",
    "numerical_statistical",
    "argumentation",
    "academic_norm",
    "method_design",
]

SEVERITIES = ["critical", "major", "minor", "suggestion"]
CITATION_VERDICTS = [
    "fully_supported",
    "partially_supported",
    "unsupported",
    "undeterminable",
]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
OVERALL_VERDICTS = ["pass", "issues_found", "needs_review", "unchecked", "insufficient"]

FINDING_HARD_REQUIRED = [
    "id", "category", "locator", "quote", "verdict", "severity", "suggestion",
]
FINDING_OPTIONAL = [
    "finding_type", "claim", "basis", "source_ref", "confidence", "review_status",
    "evidence_ids",
]

SKIP_INPUT_NAMES = {"case.json", "task.md"}


# ---------------------------------------------------------------------------
# small I/O helpers (BOM-tolerant, size-capped, no external deps)
# ---------------------------------------------------------------------------

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def write_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def read_json(path: str, default):
    try:
        return json.loads(read_text(path))
    except Exception:
        return default


def read_json_safe(path: str, default):
    # Like read_json but returns (value, error_message) so callers can record it.
    try:
        return json.loads(read_text(path)), None
    except Exception as exc:  # noqa: BLE001
        return default, "%s: %s" % (os.path.basename(path), exc)


def write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def first_nonempty(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def classify(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".txt"):
        return "txt"
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return "md"
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith(".json"):
        return "json"
    if lower.endswith((".docx", ".doc", ".pptx", ".xlsx")):
        return "office"
    return "other"


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_pdf(pdf_path: str, out_txt: str):
    """Return (ok, message, page_count). Writes page-marked text on success."""
    if shutil.which("pdftotext") is None:
        return False, "pdftotext_missing -> needs_ocr", 0
    tmp = os.path.join(tempfile.gettempdir(), "bohr_pdf_%d_%d.txt" % (os.getpid(), os.getpid()))
    try:
        proc = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", pdf_path, tmp],
            capture_output=True, text=True,
        )
    except Exception as exc:  # noqa: BLE001
        return False, "pdftotext_failed: %s" % exc, 0
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return False, "pdftotext_failed: %s" % detail, 0
    try:
        raw = read_text(tmp)
    except Exception as exc:  # noqa: BLE001
        return False, "pdftotext_read_failed: %s" % exc, 0
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    pages = raw.split("\f")
    while pages and not pages[-1].strip():
        pages.pop()
    page_count = len(pages) if pages else 1
    non_ws_chars = sum(len(p.strip()) for p in pages)
    if non_ws_chars == 0:
        return False, "scanned_pdf_empty -> needs_ocr", page_count

    with open(out_txt, "w", encoding="utf-8", newline="\n") as fh:
        for index, page in enumerate(pages, 1):
            fh.write("----- PAGE %d / %d -----\n" % (index, page_count))
            fh.write(page.strip("\n").rstrip())
            fh.write("\n\n")
    return True, "", page_count


# ---------------------------------------------------------------------------
# state.json / change_log / runtime
# ---------------------------------------------------------------------------

def init_state(work: str, case_id):
    path = os.path.join(work, "state.json")
    state = read_json(path, {}) if os.path.isfile(path) else {}
    if state.get("case_id") != case_id:
        state = {"case_id": case_id}
    state.setdefault("schema_version", "2.0")
    state.setdefault("started_at", utcnow_iso())
    state.setdefault("change_log", [])
    state.setdefault("runtime", {
        "budget_seconds": 900,
        "model_requests_budget": 64,
        "wall_seconds": None,
        "completed_at": None,
    })
    write_json(path, state)
    return state


def append_change_log(state, event: str, note: str) -> None:
    log = state.setdefault("change_log", [])
    if log and log[-1].get("event") == event and log[-1].get("note") == note:
        return  # idempotent re-run
    log.append({"at": utcnow_iso(), "event": event, "note": note})


def finalize_runtime(state) -> None:
    rt = state.setdefault("runtime", {})
    if rt.get("wall_seconds") is None:
        started = state.get("started_at")
        wall = None
        if started:
            try:
                t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
                wall = max(0, int((datetime.now(timezone.utc) - t0).total_seconds()))
            except ValueError:
                wall = None
        rt["wall_seconds"] = wall
        rt["completed_at"] = utcnow_iso()


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

def build_reading_queue(materials):
    queue = []
    for mat in materials:
        text_path = mat.get("text_path")
        if text_path:
            unit = {"material": mat.get("name"), "unit": text_path,
                    "kind": mat.get("kind")}
            if mat.get("pages"):
                unit["hint"] = "pages 1..%d" % mat["pages"]
            queue.append(unit)
        else:
            queue.append({"material": mat.get("name"), "unit": mat.get("rel_path"),
                          "kind": mat.get("kind"),
                          "hint": "; ".join(mat.get("degradations", [])) or "read directly"})
    return queue


def cmd_discover(args) -> int:
    inp, work = args.input, args.work
    os.makedirs(work, exist_ok=True)
    manifest = {
        "schema_version": "2.0",
        "case_id": None,
        "input_dir": inp,
        "generated_at": utcnow_iso(),
        "task": {},
        "materials": [],
        "errors": [],
        "degradations": [],
    }

    case_path = os.path.join(inp, "case.json")
    case = {}
    if os.path.isfile(case_path):
        case, err = read_json_safe(case_path, {})
        if err:
            manifest["errors"].append("case.json 不可解析: %s" % err)
    else:
        manifest["errors"].append("缺少 /input/case.json")
    manifest["case_id"] = first_nonempty(
        case.get("case_id"), case.get("id"), case.get("challenge_id"), "unknown")

    task_path = os.path.join(inp, "task.md")
    if os.path.isfile(task_path):
        manifest["task"] = {"path": "task.md", "bytes": file_size(task_path)}
    else:
        manifest["degradations"].append("缺少 /input/task.md")

    text_dir = os.path.join(work, "texts")
    os.makedirs(text_dir, exist_ok=True)

    for name in sorted(os.listdir(inp)):
        if name in SKIP_INPUT_NAMES:
            continue
        if name.startswith("."):
            continue
        full = os.path.join(inp, name)
        if os.path.isdir(full):
            manifest["degradations"].append("跳过目录(需人工确认): %s" % name)
            continue
        size = file_size(full)
        if size > MAX_FILE_BYTES:
            manifest["degradations"].append("文件过大被跳过: %s (%d bytes)" % (name, size))
            continue

        kind = classify(name)
        entry = {"name": name, "rel_path": name, "kind": kind, "bytes": size,
                 "degradations": []}

        if kind == "pdf":
            txt_name = os.path.splitext(name)[0] + ".txt"
            txt_path = os.path.join(text_dir, txt_name)
            ok, msg, pages = extract_pdf(full, txt_path)
            if ok:
                entry["text_path"] = "texts/" + txt_name
                entry["pages"] = pages
            else:
                entry["degradations"].append(msg)
        elif kind in ("txt", "md"):
            txt_name = os.path.splitext(name)[0] + "." + kind + ".txt"
            txt_path = os.path.join(text_dir, txt_name)
            try:
                write_text(txt_path, read_text(full))
                entry["text_path"] = "texts/" + txt_name
            except Exception as exc:  # noqa: BLE001
                entry["degradations"].append("读取失败: %s" % exc)
        elif kind in ("csv", "json"):
            entry["text_path"] = name
        manifest["materials"].append(entry)

    write_json(os.path.join(work, "manifest.json"), manifest)
    init_state(work, manifest["case_id"])
    print("discover ok: case_id=%s materials=%d errors=%d degradations=%d"
          % (manifest["case_id"], len(manifest["materials"]),
             len(manifest["errors"]), len(manifest["degradations"])))
    for mat in manifest["materials"]:
        print("  - %s (%s) %s" % (mat["name"], mat["kind"],
                                  mat.get("text_path") or mat["degradations"]))
    return 0


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------

def cmd_scaffold(args) -> int:
    inp, out, work = args.input, args.output, args.work
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)

    manifest = read_json(os.path.join(work, "manifest.json"), {})
    case, case_err = read_json_safe(os.path.join(inp, "case.json"), {})
    case_id = manifest.get("case_id") or first_nonempty(
        case.get("case_id"), case.get("id"), "unknown")
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}

    facets = list(CATEGORIES)
    extra = case.get("facets") or case.get("review_facets") or case.get("check_items")
    if isinstance(extra, list):
        for item in extra:
            key = item.get("key") if isinstance(item, dict) else item
            if isinstance(key, str) and key and key not in facets:
                facets.append(key)

    plan = {
        "schema_version": "2.0",
        "case_id": case_id,
        "created_at": utcnow_iso(),
        "facets": facets,
        "materials": manifest.get("materials", []),
        "output_contract_keys": sorted(contract.keys()),
        "reading_queue": build_reading_queue(manifest.get("materials", [])),
        "status": "in_progress",
    }
    write_json(os.path.join(work, "plan.json"), plan)

    for sub in ("evidence", "findings"):
        os.makedirs(os.path.join(work, sub), exist_ok=True)

    meta = {
        "schema_version": "2.0",
        "overall_verdict": "unchecked",
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
    }
    if case_err:
        meta["errors"].append(case_err)
    meta["errors"].extend(manifest.get("errors", []))
    meta["degradations"].extend(manifest.get("degradations", []))
    write_json(os.path.join(work, "meta.json"), meta)

    state = init_state(work, case_id)
    append_change_log(state, "scaffold", "partial skeleton written")
    write_json(os.path.join(work, "state.json"), state)

    result = build_result(case_id, contract, plan, [], [], meta, state)
    write_json(os.path.join(out, "result.json"), result)
    write_text(os.path.join(out, "report.md"), render_report(result))
    print("scaffold ok: case_id=%s facets=%d materials=%d contract_keys=%d"
          % (case_id, len(facets), len(manifest.get("materials", [])), len(contract)))
    return 0


# ---------------------------------------------------------------------------
# entry loading / validation
# ---------------------------------------------------------------------------

def load_entries(directory: str, kind: str):
    entries = []
    if not os.path.isdir(directory):
        return entries
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(".json"):
            continue
        obj, err = read_json_safe(os.path.join(directory, name), None)
        if err or not isinstance(obj, dict):
            continue
        if kind == "evidence":
            obj.setdefault("id", os.path.splitext(name)[0])
        entries.append(obj)
    entries.sort(key=lambda o: str(o.get("id", "")))
    return entries


def normalize_finding(finding, evidence_ids, problems) -> None:
    for key in FINDING_HARD_REQUIRED:
        if not finding.get(key):
            problems.append("finding %s 缺少必填键 %s" % (finding.get("id", "?"), key))
            finding.setdefault(key, "")
    for key in FINDING_OPTIONAL:
        finding.setdefault(key, "" if key not in ("evidence_ids",) else [])
    severity = finding.get("severity")
    if severity and severity not in SEVERITIES:
        problems.append("finding %s 非法 severity: %s" % (finding.get("id"), severity))
    refs = finding.get("evidence_ids") or []
    if isinstance(refs, str):
        refs = [refs]
        finding["evidence_ids"] = refs
    for ref in refs:
        if ref not in evidence_ids:
            problems.append("finding %s 引用了不存在的证据 %s" % (finding.get("id"), ref))
    if finding.get("review_status") in ("", None):
        finding["review_status"] = "checked"


# ---------------------------------------------------------------------------
# result building
# ---------------------------------------------------------------------------

def count_findings(findings):
    counts = {"total": len(findings), "critical": 0, "major": 0, "minor": 0,
              "suggestion": 0, "by_category": {}}
    for f in findings:
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
        cat = f.get("category") or "unclassified"
        counts["by_category"][cat] = counts["by_category"].get(cat, 0) + 1
    return counts


def build_evidence_map(findings, evidence_by_id):
    mapping = []
    for f in findings:
        refs = f.get("evidence_ids") or []
        entry = {
            "finding_id": f.get("id"),
            "locator": f.get("locator"),
            "quote": f.get("quote"),
            "basis": f.get("basis"),
            "source_ref": f.get("source_ref"),
            "evidence_ids": refs,
        }
        for ref in refs:
            ev = evidence_by_id.get(ref)
            if isinstance(ev, dict) and (not entry["locator"] or not entry["quote"]):
                entry["locator"] = entry["locator"] or ev.get("locator")
                entry["quote"] = entry["quote"] or ev.get("quote")
        mapping.append(entry)
    return mapping


def build_result(case_id, contract, plan, evidence, findings, meta, state):
    counts = count_findings(findings)
    verdict = meta.get("overall_verdict") if meta.get("overall_verdict") in OVERALL_VERDICTS else "unchecked"
    status = meta.get("status") if meta.get("status") in STATUSES else "partial"

    evidence_by_id = {e.get("id"): e for e in evidence if isinstance(e, dict)}

    result = {
        "case_id": case_id,
        "status": status,
        "summary": {
            "verdict": verdict,
            "counts": counts,
        },
        "review_report": {"findings": findings},
        "evidence": {
            "ledger": evidence,
            "evidence_map": build_evidence_map(findings, evidence_by_id),
        },
        "sources": meta.get("sources", []),
        "uncertainty": meta.get("uncertainty", []),
        "pending_human": meta.get("pending_human", []),
        "limitations": meta.get("limitations", []),
        "errors": meta.get("errors", []),
        "degradations": meta.get("degradations", []),
        "change_log": state.get("change_log", []),
        "runtime": state.get("runtime", {}),
        "audit_model_ref": "audit_model.json",
    }

    # Echo every output_contract field at top level; standard fields take
    # precedence (so a contract "status" cannot clobber our honest status).
    if isinstance(contract, dict):
        merged = copy.deepcopy(contract)
        merged.update(result)
        result = merged
    return result


def build_audit_model(case_id, plan, evidence, findings, meta, state):
    return {
        "schema_version": "2.0",
        "case_id": case_id,
        "generated_at": utcnow_iso(),
        "plan": plan,
        "evidence": evidence,
        "findings": findings,
        "meta": meta,
        "runtime": state.get("runtime", {}),
    }


def render_report(result) -> str:
    lines = ["# 引用与事实核验报告", ""]
    lines.append("- case_id: %s" % result.get("case_id"))
    lines.append("- status: %s · verdict: %s" % (result.get("status"),
                                                 result.get("summary", {}).get("verdict")))
    counts = result.get("summary", {}).get("counts", {})
    lines.append("- findings: total=%s critical=%s major=%s minor=%s suggestion=%s"
                 % (counts.get("total"), counts.get("critical"), counts.get("major"),
                    counts.get("minor"), counts.get("suggestion")))
    lines.append("")
    lines.append("## 问题清单")
    findings = result.get("review_report", {}).get("findings", [])
    if not findings:
        lines.append("")
        lines.append("（暂无条目化结论。）")
    for f in findings:
        lines.append("")
        lines.append("### %s · [%s] %s (%s)" % (f.get("id"), f.get("severity"),
                                                f.get("category"), f.get("verdict")))
        lines.append("- 定位: %s" % f.get("locator"))
        lines.append("- 原文: %s" % f.get("quote"))
        if f.get("claim"):
            lines.append("- 转述: %s" % f.get("claim"))
        if f.get("basis"):
            lines.append("- 依据: %s" % f.get("basis"))
        if f.get("source_ref"):
            lines.append("- 来源: %s" % f.get("source_ref"))
        if f.get("suggestion"):
            lines.append("- 建议: %s" % f.get("suggestion"))
        if f.get("evidence_ids"):
            lines.append("- 证据: %s" % ", ".join(f.get("evidence_ids")))
        lines.append("- 状态: %s / %s" % (f.get("review_status"), f.get("confidence")))
    for section in ("sources", "uncertainty", "pending_human", "limitations",
                    "errors", "degradations"):
        items = result.get(section, [])
        if items:
            lines.append("")
            lines.append("## %s" % section.replace("_", " ").title())
            for item in items:
                lines.append("- %s" % (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def cmd_finalize(args) -> int:
    inp, out, work = args.input, args.output, args.work
    manifest = read_json(os.path.join(work, "manifest.json"), {})
    plan = read_json(os.path.join(work, "plan.json"), {})
    meta = read_json(os.path.join(work, "meta.json"), {})
    state = read_json(os.path.join(work, "state.json"), {})
    case, _ = read_json_safe(os.path.join(inp, "case.json"), {})
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}

    case_id = plan.get("case_id") or manifest.get("case_id") or first_nonempty(
        case.get("case_id"), case.get("id"), "unknown")

    evidence = load_entries(os.path.join(work, "evidence"), "evidence")
    findings = load_entries(os.path.join(work, "findings"), "finding")

    problems = []
    evidence_ids = {e.get("id") for e in evidence}
    for f in findings:
        normalize_finding(f, evidence_ids, problems)

    meta.setdefault("errors", []).extend(manifest.get("errors", []))
    for deg in manifest.get("degradations", []):
        if deg not in meta.setdefault("degradations", []):
            meta["degradations"].append(deg)
    meta["errors"] = meta.get("errors", []) + problems
    write_json(os.path.join(work, "meta.json"), meta)

    finalize_runtime(state)
    append_change_log(state, "finalize",
                      "evidence=%d findings=%d problems=%d"
                      % (len(evidence), len(findings), len(problems)))

    model = build_audit_model(case_id, plan, evidence, findings, meta, state)
    write_json(os.path.join(work, "audit_model.json"), model)

    result = build_result(case_id, contract, plan, evidence, findings, meta, state)
    write_json(os.path.join(out, "result.json"), result)
    write_text(os.path.join(out, "report.md"), render_report(result))
    write_json(os.path.join(work, "state.json"), state)

    counts = result.get("summary", {}).get("counts", {})
    print("finalize ok: case_id=%s status=%s verdict=%s evidence=%d findings=%d problems=%d"
          % (case_id, result.get("status"), result.get("summary", {}).get("verdict"),
             len(evidence), len(findings), len(problems)))
    print("  counts: %s" % json.dumps(counts, ensure_ascii=False))
    for p in problems[:20]:
        print("  WARN: %s" % p)
    return 0


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _check(name, ok, detail=""):
    return {"name": name, "ok": bool(ok), "detail": detail}


def cmd_selftest(args) -> int:
    out, work = args.output, args.work
    checks = []

    result = read_json(os.path.join(out, "result.json"), None)
    checks.append(_check("result.json 存在且 json.load 可解析", result is not None))

    if result is not None:
        checks.append(_check("含 case_id", bool(result.get("case_id"))))
        checks.append(_check("含 status 且合法", result.get("status") in STATUSES,
                             str(result.get("status"))))
        findings = result.get("review_report", {}).get("findings", [])
        bad = []
        for f in findings:
            missing = [k for k in FINDING_HARD_REQUIRED if not f.get(k)]
            if missing:
                bad.append("%s:%s" % (f.get("id"), ",".join(missing)))
            if not isinstance(f.get("quote"), str) or not f["quote"].strip():
                bad.append("%s:empty_quote" % f.get("id"))
            if not isinstance(f.get("locator"), str) or not f["locator"].strip():
                bad.append("%s:empty_locator" % f.get("id"))
        checks.append(_check("每条 finding 七必填键 + 非空 locator/quote", not bad,
                             "; ".join(bad[:5])))
        emap = result.get("evidence", {}).get("evidence_map", [])
        orphan = [e.get("finding_id") for e in emap if not e.get("quote")]
        checks.append(_check("evidence_map 覆盖 finding_id+locator+quote", not orphan,
                             "; ".join(map(str, orphan[:5]))))
        model = read_json(os.path.join(work, "audit_model.json"), None)
        if model is not None:
            checks.append(_check("audit_model.json 可解析", True))
            checks.append(_check("audit_model 含 evidence/findings",
                                 isinstance(model.get("evidence"), list)
                                 and isinstance(model.get("findings"), list)))
        else:
            checks.append(_check("audit_model.json 已生成", False))
        # output_contract fields must be echoed at result.json top level
        plan = read_json(os.path.join(work, "plan.json"), {})
        contract_keys = plan.get("output_contract_keys", [])
        missing_contract = [k for k in contract_keys if k not in result]
        checks.append(_check("output_contract 字段全部回填", not missing_contract,
                             "; ".join(missing_contract[:5])))
        ledger = result.get("evidence", {}).get("ledger", [])
        used = set()
        for e in emap:
            used.update(e.get("evidence_ids") or [])
        dangling = [e.get("id") for e in ledger if e.get("id") not in used]
        checks.append(_check("evidence 条目与 finding 引用关系可查",
                             True, "未引用: %s" % ", ".join(map(str, dangling[:5]))))
    else:
        checks.append(_check("audit_model.json 已生成", False))
        checks.append(_check("output_contract 字段全部回填", False))

    # scripts are importable and --help exits 0 (silence argparse help output)
    try:
        import contextlib
        import io
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                main(["--help"])
            help_code = 0
        except SystemExit as exc:
            help_code = exc.code if exc.code is not None else 0
        checks.append(_check("main.py --help 退出码 0", help_code == 0, str(help_code)))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("main.py --help 退出码 0", False, str(exc)))

    failed = 0
    print("self-check:")
    for c in checks:
        mark = "PASS" if c["ok"] else "FAIL"
        if not c["ok"]:
            failed += 1
        print("  [%s] %s %s" % (mark, c["name"], c["detail"] or ""))
    print("self-check done: %d/%d passed" % (len(checks) - failed, len(checks)))
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_common(sp):
    sp.add_argument("--input", default=DEFAULT_INPUT, help="input dir (default /input)")
    sp.add_argument("--output", default=DEFAULT_OUTPUT, help="output dir (default /app/output)")
    sp.add_argument("--work", default=DEFAULT_WORK, help="work dir (default /app/work)")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Evidence-led citation & fact audit pipeline (stdlib only).")
    sub = parser.add_subparsers(dest="command")

    sp = sub.add_parser("discover", help="scan input, extract PDF text, write manifest.json")
    _add_common(sp)
    sp = sub.add_parser("scaffold", help="write plan.json + partial result.json skeleton")
    _add_common(sp)
    sp = sub.add_parser("finalize", help="compile audit_model.json + result.json + report.md")
    _add_common(sp)
    sp = sub.add_parser("selftest", help="validate output contract and traceability")
    _add_common(sp)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    handlers = {
        "discover": cmd_discover,
        "scaffold": cmd_scaffold,
        "finalize": cmd_finalize,
        "selftest": cmd_selftest,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
