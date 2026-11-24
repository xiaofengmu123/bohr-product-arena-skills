#!/usr/bin/env python3
"""main.py — thin, crash-proof CLI for the citation-tracing skill.

Subcommands
-----------
  bootstrap   Read /input, discover case.json, write a valid partial
              result.json skeleton + /work/state.json immediately.
  dedupe      Deduplicate /work/papers.json (and remap /work/edges.json),
              writing papers_deduped.json, edges_deduped.json, merges.json.
  finalize    Validate result.json, generate report.md / papers.csv /
              edges.csv / citation_graph.svg, update runtime, append
              a change_log entry. Idempotent: never deletes contract keys.

Usage
-----
  python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
  python3 scripts/main.py finalize --output /app/output --work /app/work [--input /input]

All arguments default to /input, /app/output and /app/work. Exit code is 0 on
success *and* on graceful degradation; a hard crash only occurs if Python
itself cannot start.
"""
from __future__ import annotations

import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import citation_lib as lib  # noqa: E402


DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def _arg(args, *names, default=None):
    for i, a in enumerate(args):
        if a in names and i + 1 < len(args):
            return args[i + 1]
    return default


def _help():
    print(__doc__)
    return 0


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #
def bootstrap(input_dir, output_dir, work_dir):
    lib.ensure_dir(output_dir)
    lib.ensure_dir(work_dir)

    pkg = lib.locate_input(input_dir)
    errors = []
    degradations = []
    sources = []

    case_id = None
    contract = {}
    case_meta = {}

    if pkg["case_json"]:
        obj, err = lib.read_json(pkg["case_json"])
        if err is not None:
            errors.append({"at": lib.now_utc(), "item": "case.json", "problem": err})
        elif isinstance(obj, dict):
            case_id = obj.get("case_id") or obj.get("id") or obj.get("taskId") or "unknown_case"
            contract = obj.get("output_contract") or obj.get("outputContract") or {}
            case_meta = {k: v for k, v in obj.items() if k not in ("output_contract", "outputContract")}
        else:
            errors.append({"at": lib.now_utc(), "item": "case.json",
                           "problem": "top-level JSON value is not an object"})
    else:
        errors.append({"at": lib.now_utc(), "item": "case.json",
                       "problem": "not found in input directory"})

    # Read task.md for research question / seed hints (best effort, never fatal).
    task_text = None
    if pkg["task_md"]:
        text, err = lib.read_text_robust(pkg["task_md"])
        if err is None:
            task_text = text
        else:
            degradations.append({"item": "task.md", "problem": err})

    # large/binary material files are recorded, not read here
    for f in pkg["materials"]:
        size = 0
        try:
            size = os.path.getsize(f)
        except OSError:
            pass
        if size > lib.MAX_TEXT_BYTES:
            degradations.append({"item": os.path.basename(f),
                                 "problem": f"oversized {size} bytes, skipped"})

    default_fields = lib.fill_contract_defaults(contract)

    status = "partial"
    if not pkg["case_json"] or case_id is None:
        status = "input_error"
    elif not contract:
        degradations.append({"item": "output_contract",
                             "problem": "absent or empty; result carries only meta fields"})

    result = {
        "case_id": case_id if case_id else "unknown_case",
        "status": status,
        "sources": sources,
        "uncertainty": {"conflicts": [], "missing": [], "coverage_gaps": []},
        "change_log": [{
            "at": lib.now_utc(),
            "what": "skeleton bootstrap",
            "why": "write a valid partial result before any analysis",
        }],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "limitations": {"missing_data": [], "preview_missing": False},
        "runtime_seconds": None,
        "skill": {"name": lib.SKILL_NAME, "version": lib.VERSION},
    }
    # merge contract defaults *without* overwriting the meta keys above
    for key, value in default_fields.items():
        if key not in result:
            result[key] = value

    out_path = os.path.join(output_dir, "result.json")
    write_err = lib.atomic_write_text(out_path, lib.json_dumps(result))
    if write_err:
        # last-resort: write the smallest valid JSON in-place
        minimal = {"case_id": case_id if case_id else "unknown_case",
                   "status": "input_error",
                   "errors": [{"problem": write_err}]}
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(lib.json_dumps(minimal))
        except OSError:
            pass

    state = {
        "started_at": lib.now_utc(),
        "input_dir": os.path.abspath(input_dir),
        "output_dir": os.path.abspath(output_dir),
        "work_dir": os.path.abspath(work_dir),
        "case_id": case_id,
        "contract": contract,
        "input_files": pkg,
        "case_meta": case_meta,
        "has_task_text": task_text is not None,
    }
    lib.atomic_write_text(os.path.join(work_dir, "state.json"), lib.json_dumps(state))

    print(f"bootstrap ok: case_id={case_id} status={status} files={len(pkg['materials'])} "
          f"result={out_path}")
    return 0


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #
def finalize(output_dir, work_dir, input_dir):
    result_path = os.path.join(output_dir, "result.json")
    obj, err = lib.read_json(result_path)
    if err is not None:
        # cannot finalize a broken result.json; produce a minimal valid one
        fallback = {
            "case_id": "unknown_case",
            "status": "input_error",
            "errors": [{"problem": f"result.json unreadable: {err}"}],
            "sources": [], "uncertainty": {"conflicts": [], "missing": [], "coverage_gaps": []},
            "change_log": [{"at": lib.now_utc(), "what": "finalize fallback",
                            "why": "result.json was unreadable"}],
            "pending_human": [], "degradations": [], "limitations": {"missing_data": []},
            "runtime_seconds": None,
            "skill": {"name": lib.SKILL_NAME, "version": lib.VERSION},
        }
        lib.atomic_write_text(result_path, lib.json_dumps(fallback))
        print("finalize degraded: result.json was unreadable, wrote fallback skeleton")
        return 0

    case_id = obj.get("case_id", "unknown_case")
    # make sure required meta keys exist even when the Agent deleted them
    for key, default in {
        "status": "partial",
        "sources": [],
        "uncertainty": {"conflicts": [], "missing": [], "coverage_gaps": []},
        "change_log": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": {"missing_data": []},
        "skill": {"name": lib.SKILL_NAME, "version": lib.VERSION},
    }.items():
        if key not in obj:
            obj[key] = default

    ok, issues = lib.validate_result(obj)
    if issues:
        obj.setdefault("degradations", []).extend(
            {"item": "result.json", "problem": issue} for issue in issues)

    # gather paper/edge data from the result (tolerate missing contract shape)
    papers = _find_papers(obj)
    edges = _find_edges(obj)
    seed = _seed_id(obj, papers)

    # rank in place (adds rank/score only); ids are the Agent's already-deduped ids.
    # Deduplication/remapping is a separate explicit step: `main.py dedupe`.
    if papers:
        graph = lib.build_graph(edges)
        papers = lib.rank_papers(papers, graph, seed)
        _write_contract_papers(obj, papers)

    produced = []
    produced.append(_write_report(output_dir, obj, papers, edges, seed))
    if papers:
        produced.append(_write_papers_csv(output_dir, papers))
    if edges:
        produced.append(_write_edges_csv(output_dir, edges))
    if papers or edges:
        nodes = papers or [{"id": s} for s in {e.get("source") for e in edges if isinstance(e, dict)}]
        svg = lib.render_svg(nodes, edges, seed)
        produced.append(_write_text(os.path.join(output_dir, "citation_graph.svg"), svg))

    # runtime
    state_path = os.path.join(work_dir, "state.json")
    state, _err = lib.read_json(state_path)
    started = None
    if isinstance(state, dict):
        started = state.get("started_at")
        obj.setdefault("_started_at", started)
    runtime = _compute_runtime(started)
    if runtime is not None:
        obj["runtime_seconds"] = runtime

    obj["change_log"].append({
        "at": lib.now_utc(),
        "what": "finalize",
        "why": "validate structure, generate artifacts, recompute runtime",
    })

    final_payload = lib.json_dumps(obj)
    lib.atomic_write_text(result_path, final_payload)
    print(f"finalize ok: papers={len(papers)} edges={len(edges)} artifacts=["
          + ", ".join(os.path.basename(p) for p in produced if p) + "]")
    return 0


def _seed_id(obj, papers):
    """Resolve a string seed identifier from seed dict/string or first paper."""
    s = obj.get("seed")
    if isinstance(s, dict):
        for key in ("id", "paper_id", "doi", "arxiv"):
            if s.get(key):
                return str(s[key])
    if isinstance(s, str) and s.strip():
        return s.strip()
    sid = obj.get("seed_id") or obj.get("seed_key")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    if isinstance(papers, list) and papers:
        p0 = papers[0]
        if isinstance(p0, dict) and p0.get("id"):
            return str(p0["id"])
    return "seed"


def _find_papers(obj):
    for key in ("results.papers", "papers", "results" if isinstance(obj.get("results"), list) else None):
        if not key:
            continue
        cur = obj
        for part in key.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
                break
        if isinstance(cur, list):
            return cur
    r = obj.get("results")
    if isinstance(r, dict) and isinstance(r.get("papers"), list):
        return r["papers"]
    return []


def _find_edges(obj):
    r = obj.get("results")
    if isinstance(r, dict) and isinstance(r.get("edges"), list):
        return r["edges"]
    if isinstance(obj.get("edges"), list):
        return obj["edges"]
    return []


def _write_contract_papers(obj, papers):
    """Write normalized papers back into whatever container initially held them."""
    r = obj.get("results")
    if isinstance(r, dict):
        r["papers"] = papers
        return
    if isinstance(obj.get("results"), list):
        # results-as-array contract: replace whole list (papers only)
        obj["results"] = papers
        return
    obj["papers"] = papers


def _compute_runtime(started):
    if not started:
        return None
    try:
        st = datetime.datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ")
        st = st.replace(tzinfo=datetime.timezone.utc)
        return int((datetime.datetime.now(datetime.timezone.utc) - st).total_seconds())
    except (ValueError, TypeError):
        return None


def _write_text(path, text):
    from citation_lib import atomic_write_text
    err = atomic_write_text(path, text)
    return "" if err else path


def _write_report(output_dir, obj, papers, edges, seed):
    lines = [
        "# 引文追踪报告 (自动汇总)",
        "",
        f"- case_id: {_md(obj.get('case_id'))}",
        f"- status: {_md(obj.get('status'))}",
        f"- seed: {_md(seed)}",
        f"- papers: {len(papers)}   edges: {len(edges)}",
        "",
        "## 说明",
        "本报告由 finalize 依据 result.json 确定性生成，仅用于复核；",
        "以 result.json 中的证据（locator + quote）为准。",
        "",
        "## 文献",
    ]
    for p in papers[:200]:
        title = (p.get("title") or "").strip() or "(untitled)"
        lines.append(f"- [{p.get('id')}] {title} ({p.get('year') or '?'})")
    if edges:
        lines.append("")
        lines.append("## 引用边")
        for e in edges[:200]:
            lines.append(f"- {e.get('source')} -> {e.get('target')} "
                         f"({e.get('relation', 'cites')})")
    content = "\n".join(lines) + "\n"
    return _write_text(os.path.join(output_dir, "report.md"), content)


def _write_papers_csv(output_dir, papers):
    fieldnames = ["id", "title", "year", "doi", "arxiv", "authors", "rank", "score",
                  "n_merged", "source_locator", "source_quote"]
    rows = []
    for p in papers:
        flat = dict(p)
        src = p.get("source") if isinstance(p.get("source"), dict) else {}
        flat["source_locator"] = src.get("locator") or p.get("locator") or ""
        flat["source_quote"] = src.get("quote") or p.get("quote") or ""
        rows.append(flat)
    return _write_text(os.path.join(output_dir, "papers.csv"),
                       _csv_text(fieldnames, rows))


def _write_edges_csv(output_dir, edges):
    fieldnames = ["source", "target", "relation", "source_label", "target_label",
                  "locator", "quote"]
    rows = []
    for e in edges:
        if isinstance(e, dict):
            ev = e.get("evidence") if isinstance(e.get("evidence"), dict) else {}
            flat = dict(e)
            flat["locator"] = ev.get("locator") or e.get("locator") or ""
            flat["quote"] = ev.get("quote") or e.get("quote") or ""
            rows.append(flat)
    return _write_text(os.path.join(output_dir, "edges.csv"), _csv_text(fieldnames, rows))


def _csv_text(fieldnames, rows):
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    text = buf.getvalue()
    # add BOM for spreadsheet apps
    if not text.startswith("\ufeff"):
        text = "\ufeff" + text
    return text


def _md(v):
    return str(v).replace("|", "\\|") if v is not None else ""


def dedupe(work_dir):
    """Run identity deduplication on intermediate files in the work directory.

    Reads ``work/papers.json`` (list) and ``work/edges.json`` (optional list),
    dedupes papers, remaps edge endpoints, and writes:
      * ``work/papers_deduped.json``
      * ``work/edges_deduped.json`` (only if edges were present)
      * ``work/merges.json`` (audit trail)
    Never guesses unknown endpoints; exit code 0 even with missing inputs.
    """
    lib.ensure_dir(work_dir)
    papers, err = lib.read_json(os.path.join(work_dir, "papers.json"))
    if err is not None or not isinstance(papers, list):
        papers = []
    edges, _ = lib.read_json(os.path.join(work_dir, "edges.json"))
    if not isinstance(edges, list):
        edges = []

    merged, merges, mapping = lib.dedupe_papers(papers)
    remapped = lib.remap_edges(edges, mapping)

    lib.atomic_write_text(os.path.join(work_dir, "papers_deduped.json"),
                          lib.json_dumps(merged))
    lib.atomic_write_text(os.path.join(work_dir, "merges.json"), lib.json_dumps(merges))
    if edges:
        lib.atomic_write_text(os.path.join(work_dir, "edges_deduped.json"),
                              lib.json_dumps(remapped))
    print(f"dedupe ok: raw={len(papers)} merged={len(merged)} merges={len(merges)} "
          f"edges={len(edges)}")
    return 0


# --------------------------------------------------------------------------- #
# entry
# --------------------------------------------------------------------------- #
def main(argv):
    if not argv or argv[0] in ("--help", "-h", "help"):
        return _help()
    cmd = argv[0]
    args = argv[1:]

    input_dir = _arg(args, "--input", "-i", default=DEFAULT_INPUT)
    output_dir = _arg(args, "--output", "-o", default=DEFAULT_OUTPUT)
    work_dir = _arg(args, "--work", "-w", default=DEFAULT_WORK)

    if cmd == "bootstrap":
        return bootstrap(input_dir, output_dir, work_dir)
    if cmd == "finalize":
        return finalize(output_dir, work_dir, input_dir)
    if cmd == "dedupe":
        return dedupe(work_dir)
    print(f"unknown command: {cmd}", file=sys.stderr)
    _help()
    return 2


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except SystemExit as exc:
        raise
    except Exception as exc:  # never crash the harness
        print(f"main.py unexpected error: {exc}", file=sys.stderr)
        code = 0
    sys.exit(code)