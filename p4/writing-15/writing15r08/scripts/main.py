#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""long-doc-writing skill: input discovery, inventory, partial-result skeleton, result assembly.

仅依赖 Python 标准库。职责:
  scan     发现输入、构建 inventory、初始化 change_log、落盘 partial result 骨架
  assemble 合并产物/证据/检查报告/变更记录，写最终 /app/output/result.json

健壮性约定:
  - 文本按 utf-8-sig / utf-8 / latin-1 顺序降级读取
  - 单文件超过 MAX_FILE_BYTES 跳过并记录
  - JSON 损坏时尝试截断修复，再失败则记录 error 不崩溃
  - 写盘一律"临时文件 + os.replace"，避免留下半成品
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+)$")
NUM_SEC_RE = re.compile(r"^\s*((?:\d{1,2})(?:\.\d{1,2}){0,3})\s+(\S.*)$")
CN_SEC_RE = re.compile(r"^\s*([一二三四五六七八九十]+[、.])\s*(\S.*)$")

ARTIFACT_EXT = {
    ".md": "text", ".txt": "text", ".json": "data", ".csv": "data",
    ".docx": "office", ".pptx": "office", ".xlsx": "office",
    ".svg": "diagram", ".pdf": "preview", ".png": "preview", ".jpg": "preview",
}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, cap=MAX_FILE_BYTES):
    """Return (text_or_None, error_or_None)."""
    p = Path(path)
    try:
        if p.stat().st_size > cap:
            return None, "size {} > cap {}".format(p.stat().st_size, cap)
    except OSError as e:
        return None, str(e)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return p.read_text(encoding=enc), None
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return None, str(e)
    return None, "no decodable encoding"


def load_json(path):
    """Return (object_or_None, error_or_None); tolerate BOM/bad encoding/bad JSON."""
    if isinstance(path, (str, Path)):
        txt, err = read_text(path)
        if err:
            return None, err
    else:
        txt = path
    try:
        return json.loads(txt), None
    except (json.JSONDecodeError, ValueError) as e:
        # 容错：尝试在最后一个 } 或 ] 处截断修复
        for marker in ("}", "]"):
            idx = txt.rfind(marker)
            if idx >= 0:
                try:
                    return json.loads(txt[: idx + 1]), "truncated JSON repaired"
                except (json.JSONDecodeError, ValueError):
                    continue
        return None, "invalid JSON: {}".format(e)


def write_json_atomic(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(p))
    return str(p)


def detect_sections(text):
    sections = []
    for i, line in enumerate(text.splitlines(), 1):
        m = HEADING_RE.match(line)
        if m:
            sections.append({"line": i, "level": len(m.group(1)),
                             "title": m.group(2).strip(), "num": None})
            continue
        m = NUM_SEC_RE.match(line)
        if m:
            sections.append({"line": i, "level": m.group(1).count(".") + 1,
                             "title": m.group(2).strip(), "num": m.group(1)})
            continue
        m = CN_SEC_RE.match(line)
        if m:
            sections.append({"line": i, "level": 1, "title": m.group(2).strip(),
                             "num": m.group(1)})
    return sections


def classify(rel):
    name = rel.lower().replace("\\", "/")
    base = name.rsplit("/", 1)[-1]
    if base == "case.json":
        return "case"
    if base == "task.md":
        return "task"
    if "workorder" in base and base.endswith(".json"):
        return "workorder"
    return "material"


def discover(input_dir):
    root = Path(input_dir)
    if not root.exists():
        return [], [{"relpath": str(root), "error": "input dir missing: {}".format(input_dir)}]
    files, errors = [], []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root).as_posix()
            size = p.stat().st_size
        except OSError as e:
            errors.append({"relpath": str(p), "error": str(e)})
            continue
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        kind = p.suffix.lower().lstrip(".")
        item = {"relpath": rel, "kind": kind, "role": classify(rel), "size": size,
                "char_count": None, "sha256": None, "sections": [], "read_error": None}
        if kind in ("md", "txt", "json"):
            txt, err = read_text(p)
            if err:
                item["read_error"] = err
                errors.append({"relpath": rel, "error": err})
            else:
                item["char_count"] = len(txt)
                item["sha256"] = hashlib.sha256(txt.encode("utf-8", "replace")).hexdigest()
                if kind in ("md", "txt"):
                    item["sections"] = detect_sections(txt)[:200]
                elif kind == "json":
                    try:
                        json.loads(txt)
                    except (json.JSONDecodeError, ValueError) as je:
                        item["read_error"] = "invalid JSON: {}".format(je)
                        errors.append({"relpath": rel, "error": item["read_error"]})
        else:
            try:
                item["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError as e:
                item["read_error"] = str(e)
                errors.append({"relpath": rel, "error": str(e)})
        files.append(item)
    return files, errors


def fingerprint(files):
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x.get("relpath", "")):
        h.update((f.get("relpath", "") + "\x00" + str(f.get("size", "")) + "\x00"
                  + str(f.get("sha256") or "") + "\x00").encode("utf-8", "replace"))
    return h.hexdigest()[:32]


def read_case(input_dir):
    p = Path(input_dir) / "case.json"
    if not p.exists():
        return None, None, "case.json missing"
    obj, err = load_json(p)
    if obj is None:
        return None, None, err or "case.json unreadable"
    if not isinstance(obj, dict):
        return None, None, "case.json is not a JSON object"
    case_id = obj.get("case_id") or obj.get("id") or obj.get("caseId")
    return case_id, obj.get("output_contract"), None


def build_skeleton(case_id, contract, files, errors, status="partial"):
    r = {
        "case_id": case_id,
        "status": status,
        "artifacts": [],
        "evidence_map": [],
        "sources": files if isinstance(files, list) else [],
        "change_log": [],
        "pending_human": [],
        "uncertainty": [],
        "limitations": [],
        "errors": [e.get("error") or e.get("relpath") for e in errors if isinstance(e, dict)],
        "degradations": [],
        "runtime": {"started_at": now_iso(), "finished_at": None, "phase": "skeleton"},
    }
    if isinstance(contract, dict):
        for k, v in contract.items():
            if k not in r:
                r[k] = [] if isinstance(v, list) else None
    return r


def cmd_scan(args):
    files, errors = discover(args.input)
    case_id, contract, case_err = read_case(args.input)
    if case_err:
        errors.append({"relpath": "case.json", "error": case_err})
    work, out = Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    inventory = {
        "case_id": case_id,
        "fingerprint": fingerprint(files),
        "scanned_at": now_iso(),
        "input_dir": str(args.input),
        "output_contract": contract,
        "files": files,
        "errors": errors,
    }
    write_json_atomic(work / "inventory.json", inventory)

    cl = load_json(work / "change_log.json")[0] if (work / "change_log.json").exists() else None
    if not isinstance(cl, list):
        cl = []
    cl.append({"step": "scan", "time": now_iso(), "action": "inventory + partial skeleton",
               "target": "work/inventory.json; output/result.json",
               "note": "{} files, {} read errors".format(len(files), len(errors))})
    write_json_atomic(work / "change_log.json", cl)

    res_path = out / "result.json"
    if not res_path.exists():
        materials = [f for f in files if f.get("role") == "material"]
        init_status = "insufficient_evidence" if not materials else "partial"
        write_json_atomic(res_path, build_skeleton(case_id, contract, files, errors,
                                                   status=init_status))

    print(json.dumps({"ok": True, "case_id": case_id, "files": len(files),
                      "errors": len(errors), "inventory": str(work / "inventory.json"),
                      "result": str(res_path)}, ensure_ascii=False))
    return 0


def collect_artifacts(out_dir):
    root = Path(out_dir)
    arts = []
    if not root.exists():
        return arts
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name == "result.json":
            continue
        ext = p.suffix.lower()
        if ext in ARTIFACT_EXT:
            try:
                arts.append({"path": p.as_posix(), "type": ARTIFACT_EXT[ext],
                             "size": p.stat().st_size})
            except OSError:
                continue
    return arts


def derive_status(inv, artifacts, check, meta):
    materials = [f for f in inv.get("files", []) if f.get("role") == "material"]
    if not materials and not artifacts:
        return "insufficient_evidence", "no source materials and no artifacts"
    if not artifacts:
        return "partial", "no target artifacts yet"
    blocking = 0
    if isinstance(check, dict):
        for iss in check.get("issues", []):
            if iss.get("severity") == "error":
                blocking += 1
    if blocking:
        return "partial", "{} blocking check issues remain".format(blocking)
    return "ok", "artifacts present, no blocking check issues"


def cmd_assemble(args):
    work, out = Path(args.work), Path(args.output)
    inv = load_json(work / "inventory.json")[0] if (work / "inventory.json").exists() else None
    if not isinstance(inv, dict):
        inv = {}
    case_id = inv.get("case_id")
    contract = inv.get("output_contract")
    evidence = load_json(work / "evidence.json")[0] if (work / "evidence.json").exists() else None
    meta = load_json(work / "delivery_meta.json")[0] if (work / "delivery_meta.json").exists() else None
    cl = load_json(work / "change_log.json")[0] if (work / "change_log.json").exists() else None
    check = load_json(work / "check_report.json")[0] if (work / "check_report.json").exists() else None

    artifacts = collect_artifacts(out)
    status, reasoning = derive_status(inv, artifacts, check, meta)

    r = build_skeleton(case_id, contract, inv.get("files", []), inv.get("errors", []))
    r["status"] = status
    r["artifacts"] = artifacts
    if isinstance(evidence, dict) and isinstance(evidence.get("entries"), list):
        r["evidence_map"] = evidence["entries"]
    r["sources"] = inv.get("files", [])
    if isinstance(meta, dict):
        for k in ("uncertainty", "pending_human", "limitations"):
            if isinstance(meta.get(k), list):
                r[k] = meta[k]
        ocv = meta.get("output_contract_values")
        if isinstance(ocv, dict):
            for k, v in ocv.items():
                if isinstance(contract, dict) and k in contract:
                    r[k] = v
        if meta.get("status"):
            r["status"] = meta["status"]
            r["status_override"] = {"status": meta["status"], "reason": meta.get("reason", "")}
            reasoning = "status_override: " + str(meta.get("reason", ""))
    if isinstance(cl, list):
        r["change_log"] = cl
    if isinstance(check, dict):
        r["check_report"] = {"issues": check.get("issues", []), "counts": check.get("counts", {})}
    r["status_reason"] = reasoning
    finished = now_iso()
    started = (r.get("runtime") or {}).get("started_at") or finished
    r["runtime"] = {"started_at": started, "finished_at": finished, "phase": "final"}
    if args.note:
        if not isinstance(r["change_log"], list):
            r["change_log"] = []
        r["change_log"].append({"step": "assemble", "time": finished, "action": args.note,
                                "target": "output/result.json", "note": ""})
    write_json_atomic(out / "result.json", r)
    print(json.dumps({"ok": True, "status": r["status"], "artifacts": len(artifacts),
                      "evidence": len(r["evidence_map"]), "result": str(out / "result.json")},
                     ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="long-doc-writing: scan inputs / assemble result.json")
    ap.add_argument("command", choices=["scan", "assemble"])
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--note", default=None, help="append a change-log note (assemble)")
    args = ap.parse_args(argv)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "assemble":
        return cmd_assemble(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
