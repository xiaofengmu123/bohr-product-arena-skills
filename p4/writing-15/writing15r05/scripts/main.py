#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""longdoc-writing Skill 主编排脚本。

只依赖 Python 标准库。用法（从 Skill 包根目录执行）：

    python3 -B scripts/main.py discover  --input /input --work /app/work
    python3 -B scripts/main.py skeleton  --input /input --work /app/work --output /app/output
    python3 -B scripts/main.py finalize  --input /input --work /app/work --output /app/output
    python3 -B scripts/main.py smoke

职责：
  discover  扫描输入目录，容错读取 case.json / task.md / organizer-edit-workorder.json 与材料，
            把发现清单（含 output_contract、case_id、文件清单与 sha256）写入 work/discovery.json。
  skeleton  读取 discovery.json，立即写一份合法 partial 的 result.json 到 output，保证任何后续
            异常都不会留下空产物；原样回填 output_contract 全部键（缺值 null/[]），并附
            status/sources/uncertainty/change_log 等标准字段。
  finalize  校验 result.json（必需键齐全、JSON 合法、契约键未丢失、产物文件存在），回填运行时长，
            输出最终校验摘要。校验失败也以退出码 0 结束，问题写入 errors 字段与 stderr。
  smoke     自检：在临时目录构造最小案例，跑 discover + skeleton + finalize 全链路并断言。
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_EXTS = {".md", ".txt", ".tex", ".rst", ".json", ".csv", ".tsv", ".yaml", ".yml", ".bib"}
MATERIAL_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt", ".tex", ".rst", ".bib", ".csv", ".json"}


def eprint(*args):
    print(*args, file=sys.stderr)


def utcnow_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def read_bytes(path):
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as e:
        return None, f"read error: {e}"
    if len(raw) > MAX_FILE_BYTES:
        return None, "file exceeds 2MB cap"
    return raw, None


def decode_text(raw):
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_json(path):
    raw, err = read_bytes(path)
    if err:
        return None, err
    text = decode_text(raw)
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"bad JSON: {e}"


def read_text(path):
    raw, err = read_bytes(path)
    if err:
        return None, err
    return decode_text(raw), None


def list_files(root):
    """确定性列出 root 下所有普通文件，按相对路径排序。"""
    root = Path(root)
    if not root.exists():
        return []
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out.append(p)
    return out


def classify(relpath):
    name = relpath.name.lower()
    if name in ("case.json", "input.json"):
        return "case"
    if name == "task.md":
        return "task"
    if name in ("organizer-edit-workorder.json", "edit-workorder.json", "workorder.json"):
        return "workorder"
    ext = relpath.suffix.lower()
    if ext in MATERIAL_EXTS:
        return "material"
    return "other"


def _leaf_default(key):
    k = str(key).lower()
    if any(t in k for t in ("list", "items", "entries", "sources", "references",
                            "errors", "degradations", "records", "logs", "tasks",
                            "array", "map", "files", "chapters", "tables", "figures")):
        return []
    return None


def backfill_contract(node):
    """递归回填 output_contract：dict 保留键，leaf 用 null/[]。"""
    if isinstance(node, dict):
        return {k: backfill_contract(v) for k, v in node.items()}
    if isinstance(node, list):
        return []
    if isinstance(node, bool):
        return None
    return _leaf_default("" if node is None else str(node))


def find_case_id(case_obj, task_text, files):
    """从多个候选位置取 case_id，失败给 input_error 提示值。"""
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        if isinstance(case_obj, dict) and key in case_obj and case_obj[key] not in (None, ""):
            return str(case_obj[key])
    if task_text:
        import re
        m = re.search(r"challenge[-_]id[:：]\s*`?([A-Za-z0-9._-]+)", task_text)
        if m:
            return m.group(1)
    for f in files:
        if f.name.lower() == "case.json":
            return "case.json"
    return "unknown_case"


def find_output_contract(case_obj):
    """定位 output_contract，兼容多种键名与嵌套。"""
    if not isinstance(case_obj, dict):
        return None, None
    for key in ("output_contract", "outputContract", "output_schema", "outputSchema",
                "required_fields", "requiredFields"):
        if key in case_obj:
            return case_obj[key], key
    # 嵌套在 output.* 下
    out = case_obj.get("output")
    if isinstance(out, dict):
        for key in ("contract", "schema", "fields", "required"):
            if key in out:
                return out[key], f"output.{key}"
    return None, None


def cmd_discover(args):
    input_dir = Path(args.input)
    work_dir = Path(args.work)
    work_dir.mkdir(parents=True, exist_ok=True)

    inventory = {
        "input_dir": str(input_dir),
        "generated_at": utcnow_iso(),
        "case": None,
        "case_id": None,
        "task_text": None,
        "workorder": None,
        "output_contract": None,
        "output_contract_source": None,
        "files": [],
        "skipped": [],
        "errors": [],
        "degradations": [],
    }

    files = list_files(input_dir)
    for p in files:
        rel = p.relative_to(input_dir)
        kind = classify(rel)
        info = {
            "path": str(rel.as_posix()),
            "absolute": str(p),
            "kind": kind,
            "size_bytes": p.stat().st_size,
            "sha256": sha256_file(p),
        }
        if p.stat().st_size > MAX_FILE_BYTES:
            info["skipped"] = "over 2MB cap"
            inventory["skipped"].append(info)
            continue
        if kind == "case":
            obj, err = read_json(p)
            if err:
                inventory["errors"].append(f"{rel.as_posix()}: {err}")
                inventory["degradations"].append("case.json unreadable; proceeding with defaults")
                info["parse_error"] = err
            else:
                inventory["case"] = obj
                contract, src = find_output_contract(obj)
                inventory["output_contract"] = contract
                inventory["output_contract_source"] = src
        elif kind == "task":
            txt, err = read_text(p)
            if err:
                inventory["errors"].append(f"{rel.as_posix()}: {err}")
            else:
                inventory["task_text"] = txt
        elif kind == "workorder":
            obj, err = read_json(p)
            if err:
                inventory["errors"].append(f"{rel.as_posix()}: {err}")
                inventory["degradations"].append("workorder unreadable; treat whole source as editable")
            else:
                inventory["workorder"] = obj
                info["workorder_parsed"] = True
        else:
            if kind == "material" and rel.suffix.lower() in TEXT_EXTS:
                txt, err = read_text(p)
                info["preview"] = (txt or "")[:400] if not err else None
                info["parse_error"] = err
        inventory["files"].append(info)

    inventory["case_id"] = find_case_id(inventory.get("case"), inventory.get("task_text"), files)

    # 提取 workorder 中的编辑目标摘要（可选，供 Agent 快速定位）
    wo = inventory.get("workorder")
    edit_tasks = []
    if isinstance(wo, dict):
        for key in ("edit_targets", "editTasks", "edits", "tasks", "items", "targets"):
            v = wo.get(key)
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        edit_tasks.append({
                            "id": item.get("id") or item.get("task_id"),
                            "target": item.get("target") or item.get("file") or item.get("chapter"),
                            "action": item.get("action") or item.get("instruction") or item.get("description"),
                            "reason": item.get("reason") or item.get("motivation"),
                        })
                    elif isinstance(item, str):
                        edit_tasks.append({"id": None, "target": None, "action": item, "reason": None})
                break
    inventory["edit_tasks"] = edit_tasks

    out_path = work_dir / "discovery.json"
    out_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "case_id": inventory["case_id"],
        "output_contract_source": inventory["output_contract_source"],
        "contract_keys": list(inventory["output_contract"].keys()) if isinstance(inventory["output_contract"], dict) else None,
        "files": len(inventory["files"]),
        "edit_tasks": len(edit_tasks),
        "errors": len(inventory["errors"]),
        "discovery": str(out_path),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_skeleton(args):
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    disc_path = work_dir / "discovery.json"
    disc, err = read_json(disc_path)
    if err or not isinstance(disc, dict):
        eprint(f"skeleton: discovery.json missing/unreadable ({err}); run discover first")
        return 1

    contract = disc.get("output_contract")
    contract_filled = backfill_contract(contract) if isinstance(contract, dict) else {}

    result = {
        "case_id": disc.get("case_id") or "unknown_case",
        "status": "partial",
    }
    result.update(contract_filled)
    result.update({
        "main_output": None,
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "pending_human": [],
        "errors": list(disc.get("errors") or []),
        "degradations": list(disc.get("degradations") or []),
        "runtime": {"started_at": utcnow_iso(), "seconds": None},
        "_meta": {
            "contract_keys": list(contract.keys()) if isinstance(contract, dict) else [],
            "discovery": disc_path.name,
        },
    })

    out_path = output_dir / "result.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "status": result["status"],
        "case_id": result["case_id"],
        "contract_keys": len(result.get("_meta", {}).get("contract_keys", [])),
        "result": str(out_path),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_finalize(args):
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    disc, _ = read_json(work_dir / "discovery.json")
    result_path = output_dir / "result.json"

    if not result_path.exists():
        eprint("finalize: result.json not found")
        return 1

    result, err = read_json(result_path)
    if err or not isinstance(result, dict):
        eprint(f"finalize: result.json invalid ({err}); leave as-is")
        return 0

    problems = []

    # 1) 必需键
    for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if key not in result:
            problems.append(f"missing required key: {key}")

    if result.get("status") not in ALLOWED_STATUS:
        problems.append(f"status not in {sorted(ALLOWED_STATUS)}: {result.get('status')!r}")

    # 2) output_contract 键不能丢
    contract = disc.get("output_contract") if isinstance(disc, dict) else None
    if isinstance(contract, dict):
        for k in contract.keys():
            if k not in result:
                problems.append(f"output_contract key lost: {k}")

    # 3) 产物文件存在性
    artifacts = result.get("artifacts") or []
    missing_artifacts = []
    for a in artifacts:
        ap = Path(a) if isinstance(a, str) else None
        if ap and not ap.is_absolute():
            ap = output_dir / ap
        if ap and not ap.exists():
            missing_artifacts.append(str(a))
    if missing_artifacts:
        problems.append(f"missing artifact files: {missing_artifacts}")

    # 4) 运行时长
    started = (result.get("runtime") or {}).get("started_at")
    seconds = None
    if started:
        try:
            t0 = _dt.datetime.fromisoformat(str(started))
            seconds = max(0.0, (_dt.datetime.now(_dt.timezone.utc) - t0).total_seconds())
        except ValueError:
            pass
    result.setdefault("runtime", {})["seconds"] = seconds
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    for p in problems:
        if p not in result["errors"]:
            result["errors"].append(p)

    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "problems": problems,
        "runtime_seconds": seconds,
        "artifacts": len(artifacts),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_smoke(_args):
    """在临时目录跑最小全链路，退出码 0 表示自检通过。"""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        inp = base / "input"
        work = base / "work"
        out = base / "output"
        inp.mkdir(parents=True)
        (inp / "case.json").write_text(json.dumps({
            "case_id": "smoke-1",
            "output_contract": {"manuscript": None, "edited_chapters": []},
        }, ensure_ascii=False), encoding="utf-8")
        (inp / "task.md").write_text("# smoke\n\nchallenge_id: `smoke-1`\n", encoding="utf-8")
        (inp / "materials").mkdir()
        (inp / "materials" / "ch1.md").write_text("样本量为 120 例。\n", encoding="utf-8")

        ns = argparse.Namespace(input=str(inp), work=str(work), output=str(out))
        cmd_discover(ns)
        cmd_skeleton(ns)
        r, err = read_json(out / "result.json")
        assert err is None, err
        assert r.get("case_id") == "smoke-1", r
        assert "manuscript" in r and "edited_chapters" in r, r
        cmd_finalize(ns)
        r2, err = read_json(out / "result.json")
        assert err is None and r2.get("status") == "partial", (err, r2)
    print("smoke: ok")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="longdoc-writing 编排脚本")
    p.add_argument("command", choices=["discover", "skeleton", "finalize", "smoke"])
    p.add_argument("--input", default=os.environ.get("SKILL_INPUT", "/input"))
    p.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", "/app/output"))
    p.add_argument("--work", default=os.environ.get("SKILL_WORK", "/app/work"))
    args = p.parse_args(argv)

    fn = {"discover": cmd_discover, "skeleton": cmd_skeleton,
          "finalize": cmd_finalize, "smoke": cmd_smoke}[args.command]
    try:
        return fn(args)
    except Exception as e:  # 顶层兜底：绝不带异常退出导致 harness 失败
        eprint(f"{args.command}: unhandled {type(e).__name__}: {e}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
