#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mind-map Skill 支持库与 CLI（仅 Python 标准库，不依赖第三方包）。

子命令：
  init     扫描输入、发现并回填输出契约、立即写出 result.json 骨架 + manifest.json
  render   校验思维导图草稿(mindmap JSON)，渲染为 mindmap.json / mindmap.md / mindmap.svg，并更新 result.json
  validate 校验输出产物（result.json 可解析、节点/边合法、SVG 可解析）
  manifest 仅写出输入清单（调试用）

默认路径（SKILL.md 可按环境覆盖）：
  --input  -> /input
  --output -> /app/output
  --work   -> /app/work

契约回填规则：若输入中的 output_contract（或 contract / result_schema / output / schema）
给出字段，则原样保留全部键，缺值填 null（列表类键填 []），不改名、不删未知键。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from xml.sax.saxutils import escape as _xml_escape

PY = sys.version_info[:2]

DEFAULT_INPUT = os.environ.get("INPUT_DIR", "/input")
DEFAULT_OUTPUT = os.environ.get("OUTPUT_DIR", "/app/output")
DEFAULT_WORK = os.environ.get("WORK_DIR", "/app/work")

MAX_TEXT_FILE = 2_000_000      # 单文件文本读取上限（字节）
MAX_PREVIEW = 4000             # manifest 中每文件预览字符数
TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".bib", ".tex", ".log"}
PDF_EXT = {".pdf"}

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

ALLOWED_NODE_TYPES = {
    "theme", "concept", "definition", "background", "research_question", "goal",
    "method", "variable", "step", "control", "criterion", "evidence", "result",
    "conclusion", "limitation", "research_gap", "risk", "conflict", "task",
    "milestone", "deliverable", "owner", "deadline", "issue", "decision", "action",
    "unknown",
}

ALLOWED_RELATIONS = {
    "causal", "dependency", "compare", "temporal", "citation", "support",
    "conflict", "sequence", "example", "part_of", "attribute",
}

ALLOWED_EVIDENCE_KIND = {"fact", "summary", "inference"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}

TYPE_COLORS = {
    "theme": "#4A5568", "concept": "#3182CE", "definition": "#718096",
    "background": "#718096", "research_question": "#DD6B20", "goal": "#DD6B20",
    "method": "#2B6CB0", "variable": "#2B6CB0", "step": "#2B6CB0",
    "control": "#2B6CB0", "criterion": "#2B6CB0", "evidence": "#38A169",
    "result": "#38A169", "conclusion": "#38A169", "limitation": "#E53E3E",
    "research_gap": "#E53E3E", "risk": "#E53E3E", "conflict": "#E53E3E",
    "task": "#805AD5", "milestone": "#805AD5", "deliverable": "#805AD5",
    "action": "#805AD5", "owner": "#805AD5", "deadline": "#805AD5",
    "issue": "#805AD5", "decision": "#805AD5", "unknown": "#A0AEC0",
}

RELATION_COLORS = {
    "causal": "#DD6B20", "dependency": "#2B6CB0", "compare": "#718096",
    "temporal": "#805AD5", "citation": "#38A169", "support": "#38A169",
    "conflict": "#E53E3E", "sequence": "#805AD5", "example": "#718096",
    "part_of": "#718096", "attribute": "#718096",
}

TYPE_ORDER = {t: i for i, t in enumerate(sorted(ALLOWED_NODE_TYPES))}


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 文本 / JSON 读取（容错）
# ---------------------------------------------------------------------------
def read_text(path, max_bytes=MAX_TEXT_FILE):
    """读取文本，优先 utf-8-sig/utf-8，再降级 gb18030/latin-1。返回 (text, err)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, f"stat error: {e}"
    if size > max_bytes:
        return None, f"file too large ({size} bytes > {max_bytes})"
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read(), None
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except OSError as e:
            return None, f"open error: {e}"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), "decoded with replacement characters"
    except OSError as e:
        return None, f"open error: {e}"


def read_json(path):
    """读取 JSON，坏 JSON 返回 (None, err)，不抛异常。"""
    text, err = read_text(path)
    if err:
        return None, f"{path}: {err}"
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as e:
        return None, f"{path}: bad JSON: {e}"


def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError as e:
        return False


def write_json(obj, path):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_text(text, path):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------
def discover_input(input_dir):
    man = {
        "input_dir": input_dir,
        "files": [],
        "case": None,
        "case_path": None,
        "task_text": None,
        "task_path": None,
        "contract": None,
        "contract_source": None,
        "errors": [],
        "degradations": [],
    }
    if not os.path.isdir(input_dir):
        man["errors"].append(f"input dir not found: {input_dir}")
        return man
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        files.sort()
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            ext = os.path.splitext(fn)[1].lower()
            entry = {"rel": rel, "name": fn, "ext": ext, "kind": None, "size": 0}
            try:
                entry["size"] = os.path.getsize(full)
            except OSError:
                entry["size"] = None
            if ext in TEXT_EXT:
                text, err = read_text(full)
                entry["kind"] = "text"
                if err:
                    entry["error"] = err
                    man["degradations"].append(f"{rel}: {err}")
                else:
                    entry["preview"] = text[:MAX_PREVIEW]
                    entry["chars"] = len(text)
            elif ext in PDF_EXT:
                entry["kind"] = "pdf"
                entry["note"] = "use platform pdf-text-tool to extract text"
            else:
                entry["kind"] = "other"
                man["degradations"].append(f"{rel}: unsupported extension '{ext or '(none)'}'")
            man["files"].append(entry)
    # 定位 case.json / task.md
    for f in man["files"]:
        if f["name"].lower() == "case.json":
            man["case_path"] = os.path.join(input_dir, f["rel"])
        if f["name"].lower() == "task.md":
            man["task_path"] = os.path.join(input_dir, f["rel"])
    if man["case_path"]:
        case, err = read_json(man["case_path"])
        if err:
            man["errors"].append(err)
        elif isinstance(case, dict):
            man["case"] = case
        else:
            man["errors"].append("case.json is not a JSON object")
    if man["task_path"]:
        text, err = read_text(man["task_path"])
        if err:
            man["errors"].append(f"task.md: {err}")
        else:
            man["task_text"] = text
    # 发现输出契约
    contract = None
    source = None
    if isinstance(man["case"], dict):
        for key in ("output_contract", "contract", "result_schema", "output_schema", "output", "schema"):
            if key in man["case"] and man["case"][key] is not None:
                contract = man["case"][key]
                source = f"case.json.{key}"
                break
    if contract is None and man["task_text"]:
        m = re.search(r"```(?:json)?\s*\n\s*\{[^`]*output_contract[^`]*\}", man["task_text"], re.S)
        if m:
            try:
                contract = json.loads(m.group(0).strip().strip("`"))
                source = "task.md inline JSON"
            except (json.JSONDecodeError, ValueError):
                contract = None
    man["contract"] = contract
    man["contract_source"] = source
    return man


def extract_case_id(man):
    case = man.get("case") or {}
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id", "task_id"):
            if isinstance(case.get(key), str) and case.get(key):
                return case[key]
    if man.get("task_text"):
        m = re.search(r"challenge_id[:：\s`]+([A-Za-z0-9_\-]+)", man.get("task_text") or "")
        if m:
            return m.group(1)
    return os.path.basename(man.get("input_dir") or "case") or "case"


def default_for_contract(key, spec):
    """根据键名/类型给出 null/[]/{} 缺省。"""
    if isinstance(spec, dict):
        t = spec.get("type")
        if t == "array":
            return []
        if t == "object":
            return {}
        return None
    if key in ("nodes", "edges", "sources", "uncertainty", "pending_human",
               "errors", "degradations", "change_log", "evidence_map",
               "artifacts", "files", "items", "tasks", "risks", "relations"):
        return []
    return None


def backfill_contract(result, contract):
    """原样回填契约键：不改名、不删未知键，缺值给 null/[]。"""
    if isinstance(contract, dict):
        for key, spec in contract.items():
            if key not in result:
                result[key] = default_for_contract(key, spec)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                result.setdefault(item, None)
            elif isinstance(item, dict) and item.get("name"):
                result.setdefault(item["name"], default_for_contract(item["name"], item))
    return result


# ---------------------------------------------------------------------------
# 思维导图规范化与校验
# ---------------------------------------------------------------------------
def _as_str(v):
    return "" if v is None else str(v).strip()


def normalize_mindmap(draft):
    """规范化草稿，返回 (mindmap, errors)。不抛异常。"""
    errors = []
    if not isinstance(draft, dict):
        return None, ["draft is not a JSON object"]
    title = _as_str(draft.get("title")) or _as_str(draft.get("case_id")) or "思维导图"
    nodes_in = draft.get("nodes")
    if not isinstance(nodes_in, list):
        nodes_in = []
    root = draft.get("root")
    root_id = None
    nodes = []
    seen = set()

    def push(n):
        nid = _as_str(n.get("id"))
        if not nid:
            errors.append("node missing id; skipped")
            return
        if nid in seen:
            errors.append(f"duplicate node id '{nid}'; skipped duplicate")
            return
        seen.add(nid)
        label = _as_str(n.get("label"))
        if not label:
            errors.append(f"node '{nid}' has empty label")
        ntype = _as_str(n.get("type")) or "concept"
        if ntype not in ALLOWED_NODE_TYPES:
            errors.append(f"node '{nid}' has unknown type '{ntype}'; coerced to 'concept'")
            ntype = "concept"
        nodes.append({
            "id": nid,
            "label": label,
            "type": ntype,
            "detail": _as_str(n.get("detail")),
            "parent": _as_str(n.get("parent")),
            "evidence": normalize_evidence(n.get("evidence"), errors, f"node:{nid}"),
        })

    if isinstance(root, dict):
        root_id = _as_str(root.get("id"))
        if root_id:
            push({**root, "parent": root.get("parent", "")})
    elif isinstance(root, str):
        root_id = _as_str(root)

    for n in nodes_in:
        if isinstance(n, dict):
            push(n)
        else:
            errors.append("node entry not an object; skipped")

    if not root_id and nodes:
        # 找 parent 为空的节点，否则取第一个
        for n in nodes:
            if not n["parent"]:
                root_id = n["id"]
                break
        if not root_id:
            root_id = nodes[0]["id"]
            nodes[0]["parent"] = ""

    # 校验 parent 引用
    ids = {n["id"] for n in nodes}
    for n in nodes:
        if n["parent"] and n["parent"] not in ids:
            errors.append(f"node '{n['id']}' parent '{n['parent']}' not found; detached as root")
            n["parent"] = ""
    # 检测环
    for n in nodes:
        cur = n["parent"]
        hops = 0
        while cur:
            hops += 1
            if hops > 200:
                errors.append(f"node '{n['id']}' has a parent cycle; detached")
                n["parent"] = ""
                break
            parent = next((x for x in nodes if x["id"] == cur), None)
            cur = parent["parent"] if parent else ""

    edges_in = draft.get("edges")
    if not isinstance(edges_in, list):
        edges_in = []
    edges = []
    seen_e = set()
    for e in edges_in:
        if not isinstance(e, dict):
            errors.append("edge entry not an object; skipped")
            continue
        eid = _as_str(e.get("id"))
        if not eid:
            eid = f"e{len(edges) + 1}"
        if eid in seen_e:
            errors.append(f"duplicate edge id '{eid}'; auto-renamed")
            eid = f"{eid}-{len(edges) + 1}"
        seen_e.add(eid)
        src = _as_str(e.get("from") or e.get("source"))
        dst = _as_str(e.get("to") or e.get("target"))
        rel = _as_str(e.get("relation") or e.get("type")) or "support"
        if rel not in ALLOWED_RELATIONS:
            errors.append(f"edge '{eid}' unknown relation '{rel}'; coerced to 'support'")
            rel = "support"
        if src not in ids or dst not in ids:
            errors.append(f"edge '{eid}' references missing node; dropped")
            continue
        if src == dst:
            errors.append(f"edge '{eid}' is a self-loop; dropped")
            continue
        edges.append({
            "id": eid,
            "from": src,
            "to": dst,
            "relation": rel,
            "label": _as_str(e.get("label")),
            "evidence": normalize_evidence(e.get("evidence"), errors, f"edge:{eid}"),
        })

    return {
        "title": title,
        "root_id": root_id,
        "nodes": nodes,
        "edges": edges,
        "uncertainty": _as_list(draft.get("uncertainty")),
        "pending_human": _as_list(draft.get("pending_human")),
    }, errors


def _as_list(v):
    if isinstance(v, list):
        return [x if isinstance(x, str) else str(x) for x in v]
    if v:
        return [str(v)]
    return []


def normalize_evidence(ev, errors, where):
    if not isinstance(ev, list):
        return []
    out = []
    for item in ev:
        if not isinstance(item, dict):
            continue
        locator = _as_str(item.get("locator") or item.get("loc") or item.get("source"))
        quote = _as_str(item.get("quote") or item.get("text"))
        if not quote and not locator:
            errors.append(f"{where}: evidence missing locator and quote; dropped")
            continue
        kind = _as_str(item.get("kind")) or "fact"
        if kind not in ALLOWED_EVIDENCE_KIND:
            kind = "fact"
        conf = _as_str(item.get("confidence")) or "medium"
        if conf not in ALLOWED_CONFIDENCE:
            conf = "medium"
        out.append({"locator": locator, "quote": quote, "kind": kind, "confidence": conf})
    return out


def derive_evidence_map(mm):
    emap = []
    for n in mm["nodes"]:
        for ev in n["evidence"]:
            emap.append({**ev, "target": n["id"], "target_kind": "node"})
    for e in mm["edges"]:
        for ev in e["evidence"]:
            emap.append({**ev, "target": e["id"], "target_kind": "edge"})
    return emap


# ---------------------------------------------------------------------------
# SVG 渲染（确定性树形布局）
# ---------------------------------------------------------------------------
def _is_cjk(ch):
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0xF900 <= cp <= 0xFAFF or 0x3000 <= cp <= 0x303F)


def wrap_label(label, max_cjk=12, max_latin=26, max_lines=4):
    label = _as_str(label)
    if not label:
        return [""]
    if any(_is_cjk(c) for c in label):
        lines, cur = [], ""
        for c in label:
            cur += c
            if len(cur) >= max_cjk:
                lines.append(cur)
                cur = ""
        if cur:
            lines.append(cur)
    else:
        words = label.split()
        lines, cur = [], ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > max_latin:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w) if cur else w
        if cur:
            lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def compute_layout(mm):
    nodes = mm["nodes"]
    by_id = {n["id"]: n for n in nodes}
    roots = [n["id"] for n in nodes if not n["parent"]]
    if not roots and nodes:
        roots = [nodes[0]["id"]]
    children = {}
    for n in nodes:
        children.setdefault(n["parent"], []).append(n["id"])

    depth = {}

    def get_depth(nid, seen=()):
        if nid in depth:
            return depth[nid]
        if nid in seen or nid not in by_id:
            depth[nid] = 0
            return 0
        parent = by_id[nid]["parent"]
        if not parent or parent not in by_id:
            depth[nid] = 0
            return 0
        d = get_depth(parent, seen + (nid,)) + 1
        depth[nid] = min(d, 20)
        return depth[nid]

    for n in nodes:
        get_depth(n["id"])

    def sort_key(nid):
        n = by_id.get(nid, {})
        return (TYPE_ORDER.get(n.get("type"), 99), _as_str(n.get("label")))

    order = {}
    counter = [0]

    def assign(nid):
        ch = sorted(children.get(nid, []), key=sort_key)
        if not ch:
            order[nid] = counter[0]
            counter[0] += 1
        else:
            for c in ch:
                assign(c)
            ys = [order[c] for c in ch]
            order[nid] = sum(ys) / len(ys)

    for r in roots:
        assign(r)

    NODE_W = 232
    LINE_H = 18
    PAD = 12
    DX = 60
    DY = 48
    pos = {}
    for n in nodes:
        lines = wrap_label(n["label"])
        h = PAD * 2 + len(lines) * LINE_H
        x = DX + depth.get(n["id"], 0) * (NODE_W + 110)
        y = DY + order.get(n["id"], counter[0]) * (h + DY)
        pos[n["id"]] = {"x": x, "y": y, "w": NODE_W, "h": h, "lines": lines, "type": n["type"]}
    return pos, children, depth


def render_svg(mm):
    pos, children, depth = compute_layout(mm)
    esc = _xml_escape
    parts = []
    nodes = mm["nodes"]
    by_id = {n["id"]: n for n in nodes}

    # 层级边（parent -> child）
    for n in nodes:
        if n["parent"] and n["parent"] in pos:
            p = pos[n["parent"]]
            c = pos[n["id"]]
            x1 = p["x"] + p["w"]
            y1 = p["y"] + p["h"] / 2
            x2 = c["x"]
            y2 = c["y"] + c["h"] / 2
            mid = (x1 + x2) / 2
            path = f"M {x1:.1f} {y1:.1f} C {mid:.1f} {y1:.1f}, {mid:.1f} {y2:.1f}, {x2:.1f} {y2:.1f}"
            parts.append(f'  <path d="{path}" fill="none" stroke="#A0AEC0" stroke-width="1.3"/>')

    # 关系边（跨分支）
    for e in mm["edges"]:
        if e["from"] in pos and e["to"] in pos:
            a = pos[e["from"]]
            b = pos[e["to"]]
            x1 = a["x"] + a["w"] / 2
            y1 = a["y"] + a["h"] / 2
            x2 = b["x"] + b["w"] / 2
            y2 = b["y"] + b["h"] / 2
            color = RELATION_COLORS.get(e["relation"], "#718096")
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            parts.append(
                f'  <path d="M {x1:.1f} {y1:.1f} C {mx:.1f} {y1:.1f}, {mx:.1f} {y2:.1f}, {x2:.1f} {y2:.1f}" '
                f'fill="none" stroke="{color}" stroke-width="1.4" stroke-dasharray="5,3"/>')
            lbl = e["label"] or e["relation"]
            parts.append(
                f'  <text x="{mx:.1f}" y="{my - 6:.1f}" font-size="11" fill="{color}" '
                f'text-anchor="middle" font-family="sans-serif">{esc(lbl)}</text>')

    # 节点
    for n in nodes:
        p = pos[n["id"]]
        color = TYPE_COLORS.get(n["type"], "#718096")
        rx = p["x"]
        ry = p["y"]
        parts.append(
            f'  <rect x="{rx:.1f}" y="{ry:.1f}" width="{p["w"]}" height="{p["h"]}" rx="8" ry="8" '
            f'fill="{color}" fill-opacity="0.12" stroke="{color}" stroke-width="1.5"/>')
        ty = ry + 15
        for line in p["lines"]:
            parts.append(
                f'  <text x="{rx + p["w"] / 2:.1f}" y="{ty:.1f}" font-size="13" fill="#1A202C" '
                f'text-anchor="middle" font-family="Noto Sans CJK SC, Microsoft YaHei, sans-serif">{esc(line)}</text>')
            ty += 18

    # 图例
    legend_x = 40
    legend_y = 30
    if not nodes:
        parts.append(
            f'  <text x="60" y="60" font-size="15" fill="#A0AEC0" font-family="sans-serif">'
            f'(无节点：材料不足，未生成导图)</text>')

    # 计算画布
    if pos:
        max_x = max(p["x"] + p["w"] for p in pos.values())
        max_y = max(p["y"] + p["h"] for p in pos.values())
    else:
        max_x, max_y = 400, 200
    width = int(max_x + 60)
    height = int(max_y + 60)

    legend = "".join(
        f'<rect x="{legend_x}" y="{legend_y + i * 20}" width="12" height="12" rx="2" fill="{c}" fill-opacity="0.35"/>'
        f'<text x="{legend_x + 18}" y="{legend_y + i * 20 + 11}" font-size="11" fill="#4A5568">{esc(t)}</text>'
        for i, (t, c) in enumerate(sorted(TYPE_COLORS.items())[:14])
    )

    svg = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        f'  <rect width="100%" height="100%" fill="#FFFFFF"/>\n'
        f'  <g id="legend">{legend}</g>\n'
        f'  <g id="relations">\n' + "\n".join(parts) + "\n  </g>\n</svg>\n"
    )
    return svg, width, height


def render_markdown(mm):
    nodes = mm["nodes"]
    children = {}
    for n in nodes:
        children.setdefault(n["parent"], []).append(n["id"])
    by_id = {n["id"]: n for n in nodes}
    roots = [n["id"] for n in nodes if not n["parent"]]
    if not roots and nodes:
        roots = [nodes[0]["id"]]

    lines = [f"# {mm['title']}", ""]

    def walk(nid, level):
        n = by_id[nid]
        prefix = "  " * (level - 1) + "- "
        tag = f"[{n['type']}] "
        line = prefix + tag + (n["label"] or "(未命名)")
        if n.get("detail"):
            line += " — " + n["detail"]
        lines.append(line)
        if n.get("evidence"):
            for ev in n["evidence"]:
                loc = ev["locator"] or "(未标注位置)"
                q = ev["quote"]
                lines.append("  " * level + f"  ↳ {ev['kind']}/{ev['confidence']} @ {loc}" +
                             (f"：\"{q}\"" if q else ""))
        for c in sorted(children.get(nid, []), key=lambda cid: _as_str(by_id.get(cid, {}).get("label"))):
            walk(c, level + 1)

    for r in roots:
        walk(r, 1)

    if mm["edges"]:
        lines.append("")
        lines.append("## 关系（跨分支）")
        lines.append("")
        for e in mm["edges"]:
            src = by_id.get(e["from"], {}).get("label", e["from"])
            dst = by_id.get(e["to"], {}).get("label", e["to"])
            lines.append(f"- **{e['relation']}**：{src} → {dst}" +
                         (f"（{e['label']}）" if e.get("label") else ""))
            for ev in e.get("evidence", []):
                lines.append(f"  ↳ {ev['kind']}/{ev['confidence']} @ {ev['locator'] or '(未标注)'}" +
                             (f"：\"{ev['quote']}\"" if ev.get("quote") else ""))

    if mm["uncertainty"]:
        lines.append("")
        lines.append("## 不确定性")
        for u in mm["uncertainty"]:
            lines.append(f"- {u}")
    if mm["pending_human"]:
        lines.append("")
        lines.append("## 需人工确认")
        for p in mm["pending_human"]:
            lines.append(f"- {p}")

    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# result.json 骨架与更新
# ---------------------------------------------------------------------------
def build_skeleton(case_id, man):
    sources = [
        {"rel": f.get("rel"), "kind": f.get("kind"), "size": f.get("size")}
        for f in man.get("files", [])
    ]
    result = {
        "case_id": case_id,
        "status": "insufficient_evidence" if not man.get("files") else "partial",
        "title": None,
        "root": None,
        "nodes": [],
        "edges": [],
        "evidence_map": [],
        "artifacts": {},
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "errors": list(man.get("errors", [])),
        "degradations": list(man.get("degradations", [])),
        "change_log": [{"time": now_iso(), "event": "initialized result skeleton"}],
        "runtime": {"started_at": now_iso(), "seconds": 0, "requests": 0},
        "_skill": "mindmap-skill",
        "_schema": "references/schema.md",
    }
    backfill_contract(result, man.get("contract"))
    return result


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------
def cmd_init(args):
    man = discover_input(args.input)
    case_id = extract_case_id(man)
    result = build_skeleton(case_id, man)
    if args.contract_file:
        contract, err = read_json(args.contract_file)
        if err:
            result["errors"].append(err)
        else:
            backfill_contract(result, contract)
            man["contract"] = contract
            man["contract_source"] = args.contract_file
    ensure_dir(args.work)
    ensure_dir(args.output)
    write_json(man, os.path.join(args.work, "manifest.json"))
    write_json(result, os.path.join(args.output, "result.json"))
    print(json.dumps({
        "case_id": case_id,
        "status": result["status"],
        "n_files": len(man["files"]),
        "contract_source": man.get("contract_source"),
        "errors": result["errors"],
        "degradations": result["degradations"],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_manifest(args):
    man = discover_input(args.input)
    ensure_dir(args.work)
    write_json(man, os.path.join(args.work, "manifest.json"))
    print(json.dumps(man, ensure_ascii=False, indent=2))
    return 0


def cmd_render(args):
    draft, err = read_json(args.mindmap)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2
    mm, errors = normalize_mindmap(draft)
    if mm is None:
        print(f"ERROR: {errors}", file=sys.stderr)
        return 2
    evidence_map = derive_evidence_map(mm)

    result_path = os.path.join(args.output, "result.json")
    result = {}
    if os.path.exists(result_path):
        result, rerr = read_json(result_path)
        if rerr:
            result = {}
    if not isinstance(result, dict):
        result = {}
    result["case_id"] = result.get("case_id") or extract_case_id(discover_input(args.input))
    result["title"] = mm["title"]
    result["root"] = mm["root_id"]
    result["nodes"] = mm["nodes"]
    result["edges"] = mm["edges"]
    result["evidence_map"] = evidence_map
    result["uncertainty"] = mm["uncertainty"]
    result["pending_human"] = mm["pending_human"]
    result["errors"] = list(set((result.get("errors") or []) + errors))
    result["degradations"] = result.get("degradations") or []

    ensure_dir(args.output)
    mindmap_json_path = os.path.join(args.output, "mindmap.json")
    mindmap_md_path = os.path.join(args.output, "mindmap.md")
    mindmap_svg_path = os.path.join(args.output, "mindmap.svg")

    write_json(mm, mindmap_json_path)
    md = render_markdown(mm)
    write_text(md, mindmap_md_path)
    svg, w, h = render_svg(mm)
    write_text(svg, mindmap_svg_path)

    result["artifacts"] = {
        "mindmap_json": mindmap_json_path,
        "mindmap_md": mindmap_md_path,
        "mindmap_svg": mindmap_svg_path,
        "svg_size": {"width": w, "height": h},
    }

    status = args.status
    if status not in ALLOWED_STATUS:
        status = "partial"
    if not mm["nodes"] and status == "ok":
        status = "insufficient_evidence"
    result["status"] = status
    result["change_log"] = (result.get("change_log") or []) + [
        {"time": now_iso(), "event": f"rendered {len(mm['nodes'])} nodes / {len(mm['edges'])} edges", "status": status}
    ]
    runtime = result.get("runtime") or {}
    runtime.setdefault("started_at", now_iso())
    result["runtime"] = runtime
    write_json(result, result_path)
    print(json.dumps({
        "status": status,
        "nodes": len(mm["nodes"]),
        "edges": len(mm["edges"]),
        "evidence": len(evidence_map),
        "errors": result["errors"],
        "artifacts": result["artifacts"],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args):
    import xml.etree.ElementTree as ET
    result_path = os.path.join(args.output, "result.json")
    report = {"checks": [], "passed": True}
    if not os.path.exists(result_path):
        report["checks"].append({"check": "result.json exists", "ok": False})
        report["passed"] = False
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if args.strict else 0
    result, err = read_json(result_path)
    if err or not isinstance(result, dict):
        report["checks"].append({"check": "result.json valid object", "ok": False, "detail": err})
        report["passed"] = False
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if args.strict else 0
    report["checks"].append({"check": "result.json valid object", "ok": True})
    report["checks"].append({"check": "case_id present",
                             "ok": bool(result.get("case_id")), "detail": result.get("case_id")})
    report["checks"].append({"check": "status valid",
                             "ok": result.get("status") in ALLOWED_STATUS, "detail": result.get("status")})
    nodes = result.get("nodes") or []
    edges = result.get("edges") or []
    ids = {n.get("id") for n in nodes if isinstance(n, dict)}
    ok_nodes = all(isinstance(n, dict) and n.get("id") and n.get("label") for n in nodes)
    report["checks"].append({"check": "nodes have id+label", "ok": ok_nodes, "detail": len(nodes)})
    ok_edges = all(isinstance(e, dict) and e.get("from") in ids and e.get("to") in ids for e in edges)
    report["checks"].append({"check": "edges reference existing nodes", "ok": ok_edges, "detail": len(edges)})
    if result.get("status") == "ok" and not nodes:
        report["checks"].append({"check": "status ok implies non-empty nodes", "ok": False})
        report["passed"] = False
    else:
        report["checks"].append({"check": "status ok implies non-empty nodes", "ok": True})
    artifacts = result.get("artifacts") or {}
    svg_path = artifacts.get("mindmap_svg")
    if svg_path and os.path.exists(svg_path):
        try:
            ET.parse(svg_path)
            report["checks"].append({"check": "mindmap.svg parses", "ok": True})
        except ET.ParseError as e:
            report["checks"].append({"check": "mindmap.svg parses", "ok": False, "detail": str(e)})
            report["passed"] = False
    else:
        report["checks"].append({"check": "mindmap.svg exists", "ok": False, "detail": svg_path})
    for name in ("mindmap_json", "mindmap_md"):
        p = artifacts.get(name)
        report["checks"].append({"check": f"{name} exists", "ok": bool(p and os.path.exists(p)), "detail": p})
    if not report["passed"]:
        report["passed"] = False
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if (args.strict and not report["passed"]) else 0


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)

    ap = argparse.ArgumentParser(parents=[common],
                                 description="Mind-map Skill 支持库（仅标准库）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_init = sub.add_parser("init", parents=[common], help="扫描输入并写 result.json 骨架")
    sp_init.add_argument("--contract-file", default=None)

    sub.add_parser("manifest", parents=[common], help="仅写出输入清单")

    sp_render = sub.add_parser("render", parents=[common], help="渲染导图草稿并更新 result.json")
    sp_render.add_argument("--mindmap", default=None, help="草稿 JSON 路径（默认 <work>/mindmap_draft.json）")
    sp_render.add_argument("--status", default="partial", choices=sorted(ALLOWED_STATUS))

    sp_val = sub.add_parser("validate", parents=[common], help="校验输出产物")
    sp_val.add_argument("--strict", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "manifest":
        return cmd_manifest(args)
    if args.cmd == "render":
        if not args.mindmap:
            args.mindmap = os.path.join(args.work, "mindmap_draft.json")
        return cmd_render(args)
    if args.cmd == "validate":
        return cmd_validate(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
