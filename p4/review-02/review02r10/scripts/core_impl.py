"""core_impl.py -- event-sourced citation/fact audit pipeline (stdlib only).

Architecture (deliberately different from the reference package):

  * A single append-only event journal (/app/work/journal.jsonl) is the only
    source of truth.  Every mutation -- init / finding / set / meta -- is one
    JSON line that is appended, never rewritten, so the complete audit history
    is always recoverable and re-inspectable.
  * ``compile`` deterministically replays the journal and emits result.json +
    report.md.  The deliverable is therefore a pure function of the journal:
    re-running it on the same journal yields byte-identical content.
  * ``verify`` machine-checks the two properties reviewers care most about:
    every finding carries the seven required keys, and every ``quote`` is a
    verbatim (whitespace-normalized) substring of the extracted source text.
    Non-verbatim quotes are flagged before a human ever re-reads the paper.

Only the Python standard library is imported.  PDF text extraction shells out
to ``pdftotext`` when present and degrades to ``needs_ocr`` otherwise.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

VERSION = "2.0.0"

DEFAULT_INPUT = "/input"
DEFAULT_WORK = "/app/work"
DEFAULT_OUTPUT = "/app/output"

MAX_FILE_BYTES = 2 * 1024 * 1024

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
VERDICTS = ["fully_supported", "partially_supported", "unsupported", "undeterminable"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
SUMMARY_VERDICTS = ["pass", "issues_found", "needs_review", "unchecked", "insufficient"]
FINDING_TYPES = [
    "factual_error",
    "inconsistency",
    "unsupported_claim",
    "overreach",
    "source_unreliable",
    "missing_control",
    "norm_violation",
    "evidence_insufficient",
    "suggestion",
]
REQUIRED_FINDING_KEYS = ["id", "category", "locator", "quote", "verdict", "severity", "suggestion"]
META_KEYS = {
    "sources", "uncertainty", "pending_human", "limitations",
    "errors", "degradations", "unchecked_items", "sections", "phases",
}
SKIP_BASENAMES = {"case.json", "task.md"}

USAGE = """\
usage: main.py <command> [options]

commands:
  discover   scan input, extract PDF/text, write work/manifest.json
  init       append init event and drop a legal skeleton result.json
  add        append one finding event to work/journal.jsonl
  set        set status or verdict
  meta       append items to a list field (sources/uncertainty/...)
  compile    replay the journal -> result.json + report.md (deterministic)
  verify     machine-check required keys + verbatim quotes
  run        discover + init + compile

options:
  --input DIR     input dir (default /input)
  --work DIR      work dir  (default /app/work)
  --output DIR    output dir (default /app/output)
  --file PATH     JSON file for add / meta
  --finding JSON  inline finding object for add
  --key KEY       set/meta target key
  --value VAL     set target value
  --items JSON    meta items (JSON array or object)
  --mode MODE     meta merge|replace (default merge)
  --help, -h      show this message (exit 0)
"""

VALUE_FLAGS = {
    "--input": "input", "--work": "work", "--output": "output",
    "--key": "key", "--value": "value", "--file": "file",
    "--finding": "finding", "--items": "items", "--mode": "mode",
}

_WS = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.\.\.|…")


# --------------------------------------------------------------------------- #
# small I/O helpers
# --------------------------------------------------------------------------- #
def read_text(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_ws(s):
    return _WS.sub(" ", (s or "")).strip()


def safe_stem(path):
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(path))
    digest = hashlib.md5(path.encode("utf-8", "replace")).hexdigest()[:8]
    return "%s__%s" % (base, digest)


def find_tool(name):
    p = shutil.which(name)
    if p:
        return p
    for cand in ("/usr/bin/" + name, "/usr/local/bin/" + name):
        if os.path.exists(cand):
            return cand
    return None


# --------------------------------------------------------------------------- #
# argument parsing (manual, so global options may appear after the command)
# --------------------------------------------------------------------------- #
def parse_argv(argv):
    ns = {
        "cmd": None, "input": DEFAULT_INPUT, "work": DEFAULT_WORK,
        "output": DEFAULT_OUTPUT, "key": None, "value": None, "file": None,
        "finding": None, "items": None, "mode": "merge", "help": False,
        "unknown": None,
    }
    cmd = None
    tokens = list(argv)
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("--help", "-h"):
            ns["help"] = True
            i += 1
            continue
        if t.startswith("-"):
            key = VALUE_FLAGS.get(t)
            if key is None:
                ns["unknown"] = t
                i += 1
                continue
            if i + 1 < len(tokens):
                ns[key] = tokens[i + 1]
                i += 2
            else:
                ns[key] = ""
                i += 1
        else:
            if cmd is None:
                cmd = t
            i += 1
    ns["cmd"] = cmd
    return ns


# --------------------------------------------------------------------------- #
# PDF / text extraction
# --------------------------------------------------------------------------- #
def extract_pdf(pdf_path, texts_dir, pages_dir):
    tool = find_tool("pdftotext")
    if not tool:
        return {"status": "needs_ocr", "pages": 0, "chars": 0,
                "error": "pdftotext not found; OCR required"}
    try:
        proc = subprocess.run(
            [tool, "-enc", "UTF-8", pdf_path, "-"],
            capture_output=True, timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "pages": 0, "chars": 0, "error": str(exc)}
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        return {"status": "error", "pages": 0, "chars": 0,
                "error": err or "pdftotext failed"}
    raw = proc.stdout.decode("utf-8", "replace")
    pages = raw.split("\f")
    while pages and not pages[-1].strip():
        pages.pop()
    n = len(pages)
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    if not pages:
        pages = [""]
        n = 1
    lines = []
    for idx, page in enumerate(pages, 1):
        body = page.strip("\n").rstrip()
        lines.append("----- PAGE %d / %d -----" % (idx, n))
        lines.append(body)
        lines.append("")
    write_text(os.path.join(texts_dir, stem + ".txt"), "\n".join(lines))
    os.makedirs(pages_dir, exist_ok=True)
    for idx, page in enumerate(pages, 1):
        write_text(os.path.join(pages_dir, "%s__p%03d.txt" % (stem, idx)), page)
    chars = sum(len(p) for p in pages)
    status = "ok" if chars > 50 else "needs_ocr"
    return {"status": status, "pages": n, "chars": chars}


def walk_materials(inp):
    found = []
    if not os.path.isdir(inp):
        return found
    for root, dirs, files in os.walk(inp):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fn in sorted(files):
            if fn in SKIP_BASENAMES:
                continue
            found.append(os.path.join(root, fn))
    return found


def list_materials(inp):
    return [os.path.relpath(p, inp) for p in walk_materials(inp)]


def discover(ns):
    inp, work = ns["input"], ns["work"]
    os.makedirs(work, exist_ok=True)
    texts_dir = os.path.join(work, "texts")
    pages_dir = os.path.join(work, "pages")
    os.makedirs(texts_dir, exist_ok=True)
    os.makedirs(pages_dir, exist_ok=True)

    manifest = {"input_dir": inp, "inputs": [], "texts": [], "errors": [], "degradations": []}
    if not os.path.isdir(inp):
        manifest["errors"].append("input dir missing: %s" % inp)
        write_json(os.path.join(work, "manifest.json"), manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    for path in walk_materials(inp):
        size = os.path.getsize(path)
        ext = os.path.splitext(path)[1].lower()
        rec = {"path": os.path.relpath(path, inp), "name": os.path.basename(path),
               "size": size, "type": ext, "status": "ok"}
        if size > MAX_FILE_BYTES:
            rec["status"] = "skipped"
            manifest["degradations"].append("too large (>2MB), skipped: %s" % rec["path"])
            manifest["inputs"].append(rec)
            continue
        if ext == ".pdf":
            r = extract_pdf(path, texts_dir, pages_dir)
            rec.update(r)
            if r["status"] == "ok":
                stem = os.path.splitext(os.path.basename(path))[0]
                manifest["texts"].append(os.path.join(texts_dir, stem + ".txt"))
            else:
                manifest["degradations"].append(
                    "PDF %s: %s (%s)" % (rec["path"], r.get("status"), r.get("error", "")))
        elif ext in (".txt", ".md", ".csv", ".json", ".jsonl", ".yaml", ".yml",
                     ".xml", ".tex", ".html", ".tsv"):
            try:
                content = read_text(path)
            except Exception as exc:  # noqa: BLE001
                rec["status"] = "bad_encoding"
                manifest["errors"].append("%s: %s" % (rec["path"], exc))
                manifest["inputs"].append(rec)
                continue
            if ext in (".json", ".jsonl"):
                try:
                    json.loads(content)
                except Exception as exc:  # noqa: BLE001
                    manifest["errors"].append(
                        "bad JSON %s: %s" % (rec["path"], str(exc)[:200]))
            dst = os.path.join(texts_dir, safe_stem(path) + ".txt")
            write_text(dst, content)
            rec["chars"] = len(content)
            manifest["texts"].append(dst)
        else:
            rec["status"] = "unsupported"
            manifest["degradations"].append("unsupported type skipped: %s" % rec["path"])
        manifest["inputs"].append(rec)

    write_json(os.path.join(work, "manifest.json"), manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------- #
# journal (append-only event log) + deterministic replay
# --------------------------------------------------------------------------- #
def journal_path(work):
    return os.path.join(work, "journal.jsonl")


def append_journal(work, event):
    with open(journal_path(work), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_journal(work):
    path = journal_path(work)
    if not os.path.isfile(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:  # noqa: BLE001
                events.append({"op": "_bad_line", "lineno": lineno, "raw": line[:200]})
    return events


def item_key(x):
    if isinstance(x, dict):
        for k in ("id", "ref", "item", "locator", "finding_id", "key", "name"):
            if x.get(k) is not None:
                return (k, x.get(k))
    return ("value", x)


def replay(journal):
    state = {
        "case_id": None, "contract": {}, "inputs": [],
        "status": "partial", "verdict": "unchecked", "status_set": False,
        "findings": {}, "meta": {}, "change_log": [],
    }
    for idx, ev in enumerate(journal):
        op = ev.get("op")
        at = ev.get("ts", "")
        if op == "_bad_line":
            state["change_log"].append(
                {"seq": idx, "op": "bad_line", "subject": ev.get("lineno"), "at": at})
            continue
        if op == "init":
            state["case_id"] = ev.get("case_id")
            state["contract"] = ev.get("contract") or {}
            state["inputs"] = ev.get("inputs") or []
        elif op == "finding":
            f = ev.get("finding") or {}
            fid = f.get("id")
            if fid:
                state["findings"][fid] = f
                state["change_log"].append(
                    {"seq": idx, "op": "finding", "subject": fid, "at": at})
        elif op == "set":
            key, value = ev.get("key"), ev.get("value")
            if key == "status":
                state["status"] = value
                state["status_set"] = True
            elif key == "verdict":
                state["verdict"] = value
            state["change_log"].append(
                {"seq": idx, "op": "set", "subject": key, "at": at})
        elif op == "meta":
            key = ev.get("key")
            items = ev.get("items") or []
            mode = ev.get("mode", "merge")
            if not isinstance(items, list):
                items = [items]
            lst = state["meta"].setdefault(key, [])
            if mode == "replace":
                lst = []
            for it in items:
                if not any(item_key(x) == item_key(it) for x in lst):
                    lst.append(it)
            state["meta"][key] = lst
            state["change_log"].append(
                {"seq": idx, "op": "meta", "subject": key, "at": at})
    return state


# --------------------------------------------------------------------------- #
# corpus + verbatim quote check
# --------------------------------------------------------------------------- #
def load_corpus(work):
    texts_dir = os.path.join(work, "texts")
    parts = []
    if os.path.isdir(texts_dir):
        for fn in sorted(os.listdir(texts_dir)):
            if fn.endswith(".txt"):
                parts.append(read_text(os.path.join(texts_dir, fn)))
    corpus = norm_ws("\n".join(parts))
    return corpus, len(parts)


def quote_verbatim(quote, corpus):
    qn = norm_ws(quote)
    if not qn:
        return False
    if qn in corpus:
        return True
    segments = [s for s in _ELLIPSIS.split(qn) if s.strip()]
    if segments and all(s in corpus for s in segments):
        return True
    return False


# --------------------------------------------------------------------------- #
# compile: journal -> result.json + report.md
# --------------------------------------------------------------------------- #
def read_case(inp):
    case_id, contract = None, {}
    path = os.path.join(inp, "case.json")
    if os.path.isfile(path):
        try:
            data = load_json(path)
            case_id = data.get("case_id") or data.get("id") or None
            contract = data.get("output_contract") or {}
        except Exception:  # noqa: BLE001
            case_id, contract = None, {}
    if not case_id:
        case_id = os.path.basename(os.path.normpath(inp)) or "unknown"
    return case_id, contract


def build_result(case_id, state, contract, work):
    findings = []
    for fid in sorted(state["findings"]):
        f = dict(state["findings"][fid])
        f.setdefault("id", fid)
        findings.append(f)
    corpus, ntexts = load_corpus(work)
    for f in findings:
        q = norm_ws(f.get("quote") or "")
        if corpus:
            f["quote_verified"] = quote_verbatim(q, corpus)
        else:
            f["quote_verified"] = None
    findings.sort(key=lambda f: str(f.get("id", "")))

    counts = {"total": 0, "critical": 0, "major": 0, "minor": 0, "suggestion": 0,
              "by_category": {}, "by_finding_type": {}}
    for f in findings:
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
        counts["total"] += 1
        cat = f.get("category")
        if cat:
            counts["by_category"][cat] = counts["by_category"].get(cat, 0) + 1
        ft = f.get("finding_type")
        if ft:
            counts["by_finding_type"][ft] = counts["by_finding_type"].get(ft, 0) + 1

    errors = list(state["meta"].get("errors", []))
    for f in findings:
        missing = [k for k in REQUIRED_FINDING_KEYS if not f.get(k)]
        if missing:
            errors.append({"finding_id": f.get("id"), "issue": "missing_required_keys",
                           "detail": missing})
        if f.get("category") not in CATEGORIES:
            errors.append({"finding_id": f.get("id"), "issue": "invalid_category",
                           "detail": f.get("category")})
        if f.get("severity") not in SEVERITIES:
            errors.append({"finding_id": f.get("id"), "issue": "invalid_severity",
                           "detail": f.get("severity")})
        if f.get("verdict") not in VERDICTS:
            errors.append({"finding_id": f.get("id"), "issue": "invalid_verdict",
                           "detail": f.get("verdict")})
        if f.get("quote_verified") is False:
            errors.append({"finding_id": f.get("id"), "issue": "quote_not_verbatim",
                           "detail": "quote not found verbatim in extracted text"})

    status, verdict = state["status"], state["verdict"]
    if not state["status_set"] and not findings and not state["inputs"]:
        status, verdict = "insufficient_evidence", "insufficient"

    evidence_map = {}
    for f in findings:
        evidence_map[f["id"]] = {
            "locator": f.get("locator"),
            "quote": f.get("quote"),
            "basis": f.get("basis"),
            "source_ref": f.get("source_ref"),
            "quote_verified": f.get("quote_verified"),
        }

    checked = sorted(c for c in counts["by_category"])
    result = {
        "case_id": case_id,
        "status": status,
        "summary": {
            "verdict": verdict,
            "counts": counts,
            "coverage": {
                "categories_checked": checked,
                "categories_unchecked": [c for c in CATEGORIES if c not in checked],
                "sections": state["meta"].get("sections", []),
            },
            "notes": "",
        },
        "review_report": {
            "findings": findings,
            "unchecked_items": state["meta"].get("unchecked_items", []),
        },
        "evidence": {"evidence_map": evidence_map},
        "output_contract": contract if isinstance(contract, dict) else {},
        "sources": state["meta"].get("sources", []),
        "uncertainty": state["meta"].get("uncertainty", []),
        "pending_human": state["meta"].get("pending_human", []),
        "limitations": state["meta"].get("limitations", []),
        "errors": errors,
        "degradations": state["meta"].get("degradations", []),
        "change_log": state["change_log"],
        "runtime": {
            "budget": {"requests": 64, "seconds": 900, "tokens_per_response": 16384},
            "phases": state["meta"].get("phases", []),
            "corpus": {"text_files": ntexts, "check": "verbatim_substring"},
        },
    }
    for k, v in (contract or {}).items():
        if k not in result:
            result[k] = v
    return result


def _esc(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


def render_report(result):
    out = []
    out.append("# 引用与事实核验审查报告\n")
    out.append("- case_id: `%s`" % result.get("case_id"))
    out.append("- status: `%s`" % result.get("status"))
    s = result.get("summary", {})
    out.append("- verdict: `%s`" % s.get("verdict"))
    c = s.get("counts", {})
    out.append("- counts: total %s (critical %s / major %s / minor %s / suggestion %s)\n"
               % (c.get("total"), c.get("critical"), c.get("major"),
                  c.get("minor"), c.get("suggestion")))
    findings = result.get("review_report", {}).get("findings", [])
    out.append("## Findings (%d)\n" % len(findings))
    if findings:
        for f in findings:
            out.append("### %s — %s / %s / %s\n" % (
                f.get("id"), f.get("category"), f.get("severity"), f.get("verdict")))
            out.append("- locator: `%s`" % f.get("locator"))
            out.append("- finding_type: `%s`" % f.get("finding_type"))
            out.append("- claim: %s" % (f.get("claim") or "-"))
            out.append("- basis: %s" % (f.get("basis") or "-"))
            out.append("- source_ref: `%s`" % (f.get("source_ref") or "not_verified"))
            out.append("- review_status: `%s`" % (f.get("review_status") or "reviewed"))
            out.append("- quote_verified: `%s`" % f.get("quote_verified"))
            out.append("- suggestion: %s" % (f.get("suggestion") or "-"))
            out.append("")
            out.append("```text")
            out.append(f.get("quote") or "")
            out.append("```")
            out.append("")
    else:
        out.append("_No findings recorded yet._\n")

    ev = result.get("evidence", {}).get("evidence_map", {})
    out.append("## Evidence map (%d)\n" % len(ev))
    for fid in sorted(ev):
        e = ev[fid]
        out.append("- **%s** `%s` — source_ref `%s` — verified `%s`"
                   % (fid, e.get("locator"), e.get("source_ref"), e.get("quote_verified")))
    out.append("")

    for title, key in (("Sources", "sources"), ("Uncertainty", "uncertainty"),
                       ("Pending human", "pending_human"), ("Limitations", "limitations"),
                       ("Errors", "errors"), ("Degradations", "degradations")):
        items = result.get(key, [])
        out.append("## %s (%d)\n" % (title, len(items)))
        if items:
            for it in items:
                if isinstance(it, dict):
                    out.append("- " + "; ".join("%s=%s" % (k, _esc(v)) for k, v in it.items()))
                else:
                    out.append("- " + _esc(str(it)))
        else:
            out.append("_none_\n")
        out.append("")

    clog = result.get("change_log", [])
    out.append("## Change log (%d)\n" % len(clog))
    for e in clog[-40:]:
        out.append("- #%s %s %s @ %s" % (e.get("seq"), e.get("op"), e.get("subject"), e.get("at")))
    out.append("")
    return "\n".join(out) + "\n"


def compile_cmd(ns):
    inp, work, out = ns["input"], ns["work"], ns["output"]
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    journal = load_journal(work)
    state = replay(journal)
    case_id_fs, contract_fs = read_case(inp)
    case_id = state["case_id"] or case_id_fs
    contract = dict(state["contract"] or {})
    contract.update(contract_fs or {})
    result = build_result(case_id, state, contract, work)
    write_json(os.path.join(out, "result.json"), result)
    write_text(os.path.join(out, "report.md"), render_report(result))
    c = result["summary"]["counts"]
    print("compiled: status=%s verdict=%s findings=%d" % (
        result["status"], result["summary"]["verdict"], c["total"]))
    return 0


# --------------------------------------------------------------------------- #
# journal-writing commands
# --------------------------------------------------------------------------- #
def init_cmd(ns):
    inp, work = ns["input"], ns["work"]
    os.makedirs(work, exist_ok=True)
    os.makedirs(ns["output"], exist_ok=True)
    if not os.path.isfile(journal_path(work)):
        case_id, contract = read_case(inp)
        event = {"op": "init", "case_id": case_id, "contract": contract,
                 "inputs": list_materials(inp), "ts": now_iso()}
        append_journal(work, event)
        print("journal initialized: case_id=%s materials=%d" % (
            case_id, len(event["inputs"])))
    else:
        print("journal already exists; init skipped (idempotent)")
    return compile_cmd(ns)


def add_cmd(ns):
    work = ns["work"]
    os.makedirs(work, exist_ok=True)
    obj = None
    if ns["file"]:
        try:
            obj = load_json(ns["file"])
        except Exception as exc:  # noqa: BLE001
            print("cannot read --file: %s" % exc, file=sys.stderr)
            return 2
    elif ns["finding"]:
        try:
            obj = json.loads(ns["finding"])
        except Exception as exc:  # noqa: BLE001
            print("cannot parse --finding: %s" % exc, file=sys.stderr)
            return 2
    else:
        try:
            data = sys.stdin.read()
            if data.strip():
                obj = json.loads(data)
        except Exception as exc:  # noqa: BLE001
            print("cannot parse stdin: %s" % exc, file=sys.stderr)
            return 2
    if not isinstance(obj, dict):
        print("finding must be a JSON object", file=sys.stderr)
        return 2
    if not obj.get("id"):
        print("finding requires non-empty 'id'", file=sys.stderr)
        return 2
    missing = [k for k in REQUIRED_FINDING_KEYS if not obj.get(k)]
    if missing:
        print("warning: missing keys %s (compile will flag)" % missing, file=sys.stderr)
    append_journal(work, {"op": "finding", "finding": obj, "ts": now_iso()})
    print("added finding %s (%s)" % (obj.get("id"), obj.get("category")))
    return 0


def set_cmd(ns):
    work = ns["work"]
    key, value = ns["key"], ns["value"]
    if key not in ("status", "verdict"):
        print("set --key must be status|verdict", file=sys.stderr)
        return 2
    if key == "status" and value not in STATUSES:
        print("invalid status %r (allowed: %s)" % (value, ", ".join(STATUSES)), file=sys.stderr)
        return 2
    if key == "verdict" and value not in SUMMARY_VERDICTS:
        print("invalid verdict %r (allowed: %s)" % (value, ", ".join(SUMMARY_VERDICTS)), file=sys.stderr)
        return 2
    os.makedirs(work, exist_ok=True)
    append_journal(work, {"op": "set", "key": key, "value": value, "ts": now_iso()})
    print("set %s=%s" % (key, value))
    return 0


def meta_cmd(ns):
    work = ns["work"]
    key = ns["key"]
    if key not in META_KEYS:
        print("meta --key must be one of: %s" % ", ".join(sorted(META_KEYS)), file=sys.stderr)
        return 2
    items = None
    if ns["file"]:
        try:
            items = load_json(ns["file"])
        except Exception as exc:  # noqa: BLE001
            print("cannot read --file: %s" % exc, file=sys.stderr)
            return 2
    elif ns["items"]:
        try:
            items = json.loads(ns["items"])
        except Exception as exc:  # noqa: BLE001
            print("cannot parse --items: %s" % exc, file=sys.stderr)
            return 2
    if items is None:
        print("meta requires --file or --items", file=sys.stderr)
        return 2
    os.makedirs(work, exist_ok=True)
    append_journal(work, {"op": "meta", "key": key, "items": items,
                          "mode": ns["mode"], "ts": now_iso()})
    n = len(items) if isinstance(items, list) else 1
    print("meta %s += %d item(s)" % (key, n))
    return 0


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #
def verify_cmd(ns):
    work, out = ns["work"], ns["output"]
    result_path = os.path.join(out, "result.json")
    if not os.path.isfile(result_path):
        print("result.json missing; run compile first", file=sys.stderr)
        return 1
    result = load_json(result_path)
    corpus, _ = load_corpus(work)
    issues = []
    findings = result.get("review_report", {}).get("findings", [])
    for f in findings:
        missing = [k for k in REQUIRED_FINDING_KEYS if not f.get(k)]
        if missing:
            issues.append({"finding_id": f.get("id"), "issue": "missing_required_keys",
                           "detail": missing})
        if f.get("category") not in CATEGORIES:
            issues.append({"finding_id": f.get("id"), "issue": "invalid_category",
                           "detail": f.get("category")})
        if f.get("severity") not in SEVERITIES:
            issues.append({"finding_id": f.get("id"), "issue": "invalid_severity",
                           "detail": f.get("severity")})
        if f.get("verdict") not in VERDICTS:
            issues.append({"finding_id": f.get("id"), "issue": "invalid_verdict",
                           "detail": f.get("verdict")})
        q = norm_ws(f.get("quote") or "")
        if corpus and q and not quote_verbatim(q, corpus):
            issues.append({"finding_id": f.get("id"), "issue": "quote_not_verbatim",
                           "detail": "quote not found verbatim in extracted text"})
    report = {
        "checked": len(findings),
        "issues": issues,
        "corpus_available": bool(corpus),
        "generated_at": now_iso(),
    }
    os.makedirs(work, exist_ok=True)
    write_json(os.path.join(work, "verify_report.json"), report)
    print("verify: findings=%d issues=%d" % (len(findings), len(issues)))
    for it in issues:
        print("  - %s: %s %s" % (it.get("finding_id"), it.get("issue"), it.get("detail")))
    return 0


def run_cmd(ns):
    rc = discover(ns)
    if rc != 0:
        return rc
    rc = init_cmd(ns)
    if rc != 0:
        return rc
    return compile_cmd(ns)


HANDLERS = {
    "discover": discover,
    "init": init_cmd,
    "add": add_cmd,
    "set": set_cmd,
    "meta": meta_cmd,
    "compile": compile_cmd,
    "verify": verify_cmd,
    "run": run_cmd,
}


def main(argv):
    ns = parse_argv(argv)
    if ns["help"]:
        print(USAGE)
        return 0
    if ns["unknown"]:
        print("unknown option: %s" % ns["unknown"], file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    cmd = ns["cmd"]
    if cmd is None:
        print(USAGE, file=sys.stderr)
        return 2
    handler = HANDLERS.get(cmd)
    if handler is None:
        print("unknown command: %s" % cmd, file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    try:
        return handler(ns)
    except Exception as exc:  # noqa: BLE001
        print("command %s failed: %s" % (cmd, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
