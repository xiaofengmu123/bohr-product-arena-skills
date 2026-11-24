#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill bootstrap: discover inputs and write the first honest result skeleton.

This runs first, before any intellectual extraction. It guarantees that a valid
/app/output/result.json exists on disk within the opening minutes of the budget,
with honest status, sources, uncertainty, change_log and the original
output_contract fields echoed verbatim (values null until filled).

Standard library only. Robust against empty input, bad JSON and bad encodings.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
MAX_TEXT_BYTES = 2 * 1024 * 1024  # per-text-file cap before skipping
TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv", ".xml", ".html"}
PDF_SUFFIX = ".pdf"
DEFAULT_CONTRACT = {"deliverable": None, "evidence_map": None, "version_boundary": None}
# Fields always computed by the Skill; output_contract echo must not clobber them.
INTERNAL_KEYS = {
    "case_id", "status", "title", "artifacts", "evidence_map", "sources",
    "uncertainty", "change_log", "errors", "degradations", "pending_human",
    "limitations", "runtime", "output_contract",
}


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stdout(text):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def read_text(path):
    """Tolerant text read with a size cap. Returns (text_or_None, error)."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as e:
        return None, "无法读取 %s: %s" % (p.name, e)
    if len(data) > MAX_TEXT_BYTES:
        return None, "跳过 %s: 超过 %d 字节上限" % (p.name, MAX_TEXT_BYTES)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "已用替换字符解码 %s" % p.name


def find_key(obj, key, _depth=0):
    """Recursively find the first value for `key`."""
    if _depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key, _depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key, _depth + 1)
            if r is not None:
                return r
    return None


def resolve_case_id(case_obj, task_text):
    for k in ("case_id", "id", "challenge_id", "task_id"):
        v = find_key(case_obj, k)
        if v and not isinstance(v, (dict, list)):
            return str(v).strip()
    if task_text:
        m = re.search(r"challenge[-_]id[:\s`']+([A-Za-z0-9_\-]+)", task_text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"case[-_]id[:\s`']+([A-Za-z0-9_\-]+)", task_text, re.I)
        if m:
            return m.group(1)
    return "unknown"


def resolve_contract(case_obj):
    """Return the original output_contract fields as a dict (keys echoed)."""
    oc = find_key(case_obj, "output_contract")
    if isinstance(oc, dict):
        return {k: (v if v is not None else None) for k, v in oc.items()}
    if isinstance(oc, list):
        return {str(k): None for k in oc}
    if isinstance(oc, str) and oc.strip():
        return {oc.strip(): None}
    return {}


def scan_input(input_dir):
    """List candidate source files under the input directory."""
    base = Path(input_dir)
    if not base.exists():
        return []
    found = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        found.append({"path": str(p), "relative": str(p.relative_to(base)),
                      "suffix": p.suffix.lower(), "size": p.stat().st_size})
    return found


def extract_material_text(files, work_dir):
    """Extract plain text for the agent to read; PDFs via pdftotext if present."""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    texts, degradations = [], []
    pdftotext = shutil.which("pdftotext")
    for f in files:
        suf = f["suffix"]
        if suf in TEXT_SUFFIXES:
            text, err = read_text(f["path"])
            if err:
                degradations.append(err)
            if text is not None:
                texts.append({"file": f["relative"], "text": text, "source": "direct"})
        elif suf == PDF_SUFFIX:
            if pdftotext:
                out_txt = work / (Path(f["relative"]).stem + ".txt")
                try:
                    subprocess.run([pdftotext, "-layout", f["path"], str(out_txt)],
                                   check=True, timeout=120, capture_output=True)
                    text, err = read_text(out_txt)
                    if text is not None:
                        texts.append({"file": f["relative"], "text": text,
                                      "source": "pdftotext",
                                      "extracted": str(out_txt.relative_to(work))})
                    if err:
                        degradations.append(err)
                except Exception as e:
                    degradations.append("PDF 文本提取失败 %s: %s" % (f["relative"], e))
            else:
                degradations.append("缺少 pdftotext，未提取 PDF 文本: %s" % f["relative"])
        else:
            degradations.append("未处理二进制材料（需人工/专用工具）: %s" % f["relative"])
    return texts, degradations


def write_inventory(work_dir, case_id, contract, files, texts, degradations):
    inv = {
        "case_id": case_id,
        "output_contract": contract,
        "files": files,
        "texts": [{"file": t["file"], "source": t["source"],
                   "chars": len(t["text"]), "text": t["text"],
                   "extracted": t.get("extracted")} for t in texts],
        "degradations": degradations,
        "generated_at": utcnow(),
    }
    wp = Path(work_dir)
    wp.mkdir(parents=True, exist_ok=True)
    (wp / "inventory.json").write_text(
        json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    return inv


def write_skeleton(outdir, case_id, contract, files, texts, degradations, start_t, input_dir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    has_material = any(t["text"].strip() for t in texts)
    status = "partial" if has_material else "insufficient_evidence"
    limitations = list(degradations)
    if not has_material:
        limitations.append("输入目录未发现可读材料或材料为空，无法构建思维导图；待补充材料")
    if not files:
        limitations.append("输入目录不存在或为空（%s）" % Path(input_dir))

    result = {
        "case_id": case_id,
        "status": status,
        "title": None,
        "artifacts": {
            "mindmap_md": None, "mindmap_json": None, "mindmap_mm": None,
            "mindmap_svg": None, "mindmap_png": None,
        },
        "sources": [{"file": f["relative"], "size": f["size"],
                     "path": f["path"]} for f in files],
        "uncertainty": [],
        "evidence_map": [],
        "change_log": [{
            "at": utcnow(), "action": "skeleton_written",
            "detail": "发现 %d 个文件、%d 份可读文本；材料是否足够=%s"
                      % (len(files), len(texts), has_material),
        }],
        "errors": [],
        "degradations": list(degradations),
        "pending_human": [],
        "limitations": limitations,
        "runtime": {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_t)),
                    "finished_at": utcnow(), "seconds": round(time.time() - start_t, 1)},
        "output_contract": dict(contract),
    }
    # echo original output_contract fields at top level, values null until filled;
    # never overwrite the fields computed by the Skill itself.
    if contract:
        for k in contract:
            if k not in INTERNAL_KEYS:
                result[k] = contract[k]
    else:
        for k in DEFAULT_CONTRACT:
            if k not in INTERNAL_KEYS:
                result[k] = None
    rp = out / "result.json"
    tmp = rp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(rp)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Discover inputs and write result skeleton.")
    ap.add_argument("--input", default="/input", help="input directory (default /input)")
    ap.add_argument("--output", default="/app/output", help="output directory")
    ap.add_argument("--work", default="/app/work", help="work directory")
    args = ap.parse_args(argv)
    t0 = time.time()

    files = scan_input(args.input)
    case_obj = {}
    task_text = None
    degradations = []
    for f in files:
        name = Path(f["path"]).name
        if name == "case.json":
            text, err = read_text(f["path"])
            if err:
                degradations.append(err)
            if text is not None:
                try:
                    case_obj = json.loads(text)
                except json.JSONDecodeError as e:
                    degradations.append("case.json 无法解析: %s" % e)
        elif name in ("task.md", "task.txt", "README.md"):
            text, err = read_text(f["path"])
            if err:
                degradations.append(err)
            if text is not None:
                task_text = text

    case_id = resolve_case_id(case_obj, task_text)
    contract = resolve_contract(case_obj)
    texts, text_degradations = extract_material_text(files, args.work)
    degradations.extend(text_degradations)

    write_inventory(args.work, case_id, contract, files, texts, degradations)
    result = write_skeleton(args.output, case_id, contract, files, texts,
                            degradations, t0, args.input)
    _stdout("case_id=%s status=%s files=%d texts=%d contract_fields=%s"
            % (case_id, result["status"], len(files), len(texts),
               ",".join(contract.keys()) if contract else "(none)"))
    for d in degradations:
        _stdout("DEGRADE: " + d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
