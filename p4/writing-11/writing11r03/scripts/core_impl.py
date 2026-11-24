#!/usr/bin/env python3
"""read_case.py -- 盘点 /input 输入目录，产出确定性 case_brief.json。

只做事实盘点（case_id、output_contract 键、材料清单、编码异常、输入签名），
不翻译、不生成结论。同一输入目录重复运行产出相同的盘点内容（generated_at
仅记录时间，不参与后续判断）。
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
CASE_ID_KEYS = ("case_id", "id", "caseId", "caseid")
DEFAULT_CONTRACT = {
    "deliverables": [],
    "fact_source_map": [],
    "version_boundary": {
        "modifications": [],
        "human_confirm": [],
        "material_gaps": [],
        "anomalies": [],
        "editable": True,
    },
}


def read_text_safe(path):
    """尽力解码文本，返回 (text, encoding)；失败返回 (None, None)。"""
    try:
        raw = open(path, "rb").read()
    except OSError:
        return None, None
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def load_json_safe(path):
    text, enc = read_text_safe(path)
    if text is None:
        return None, enc
    try:
        return json.loads(text), enc
    except (json.JSONDecodeError, ValueError, TypeError):
        return None, enc


def find_case_json(root):
    for name in ("case.json", "Case.json", "case.JSON"):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return p
    return None


def iter_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            out.append(os.path.join(dirpath, fn))
    return out


def main():
    ap = argparse.ArgumentParser(description="盘点 /input，生成 case_brief.json")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--out", default="/app/work/case_brief.json")
    ap.add_argument("--brief", action="store_true", help="仅打印摘要")
    a = ap.parse_args()

    root = a.input
    files = iter_files(root) if os.path.isdir(root) else []
    case_path = find_case_json(root)

    case = None
    case_id = None
    encoding_issues = []
    if case_path:
        case, _enc = load_json_safe(case_path)
        if case is None:
            encoding_issues.append({"path": case_path, "issue": "bad_json_or_encoding"})
        elif isinstance(case, dict):
            for k in CASE_ID_KEYS:
                if k in case and case[k] is not None:
                    case_id = str(case[k])
                    break

    # output_contract：优先 case.json 声明，否则用默认三字段
    output_contract = DEFAULT_CONTRACT
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict) and oc:
            output_contract = oc
        elif isinstance(oc, list) and oc:
            output_contract = {k: [] for k in oc}

    # 材料清单：排除 case.json 与 task.*
    excluded = {os.path.basename(case_path).lower() if case_path else "",
                "task.md", "task.txt"}
    materials = []
    for p in files:
        bn = os.path.basename(p).lower()
        if bn in excluded or bn.startswith("."):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            size = -1
        materials.append({
            "path": p,
            "rel": os.path.relpath(p, root).replace("\\", "/"),
            "bytes": size,
            "ext": os.path.splitext(p)[1].lower(),
        })

    # 输入签名（全部文件字节级哈希，用于确定性核对）
    h = hashlib.sha256()
    for p in files:
        h.update(p.replace("\\", "/").encode("utf-8"))
        try:
            h.update(open(p, "rb").read())
        except OSError:
            h.update(b"<unreadable>")
    signature = h.hexdigest()

    task_path = os.path.join(root, "task.md")
    brief = {
        "case_id": case_id,
        "case_path": case_path,
        "task_path": task_path if os.path.isfile(task_path) else None,
        "output_contract_keys": sorted(output_contract.keys()),
        "output_contract": output_contract,
        "materials": materials,
        "encoding_issues": encoding_issues,
        "input_signature": signature,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    out_dir = os.path.dirname(a.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(brief, f, ensure_ascii=False, indent=2, sort_keys=True)

    if a.brief:
        print(json.dumps({
            "case_id": case_id,
            "output_contract_keys": brief["output_contract_keys"],
            "material_count": len(materials),
            "encoding_issues": len(encoding_issues),
        }, ensure_ascii=False))
    else:
        print(json.dumps(brief, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
