#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — sci-viz-robust 的防御性内核（仅 Python 标准库）。

本模块被 main.py 与 selftest.py 复用。设计原则：**任何输入都不把异常抛给外层**。
所有公开函数要么返回可用的部分结果，要么返回带 error/degradation 记录的空结果；
外层调用者据此写合法 result.json，保证 Harness 进程退出码恒为 0。

与常见绘图脚手架的不同取舍：
1. 契约来源不假设 case.json 里一定有 output_contract 键——也支持从 task.md /
   case.json 正文的表格字段（分析结果 / 质量与复现证据 / 异常说明）反推契约模板；
2. 每个文本文件按字节上限读取，超限记录"跳过"而非读取截断，避免半行数据污染统计；
3. 证据 locator 携带内容 hash，他人可核对 quote 未被篡改；
4. 初始化即落盘 work/manifest.json（输入哈希 + 随机种子 + 文件清单），支持重跑比对。
"""
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

MAX_FILE_BYTES = 2 * 1024 * 1024          # 单文本文件读取上限，超限跳过并记录
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
RESERVED_KEYS = ("case_id", "status", "sources", "uncertainty", "pending_human",
                 "change_log", "errors", "degradations", "runtime", "artifacts")
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json")
DATA_EXTS = (".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".parquet")

CONTRACT_KEYS = ("output_contract", "outputContract", "result_contract",
                 "resultContract", "contract", "result_schema", "resultSchema",
                 "expected_output", "expectedOutput", "schema")


# --------------------------------------------------------------------------- #
# 时间 / 编码 / 哈希
# --------------------------------------------------------------------------- #
def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path, limit=MAX_FILE_BYTES):
    """对文件内容取哈希。超限时哈希"前 limit 字节 + 字节数标记"，并返回说明。

    返回 (hexdigest, note)。note 为空表示全量哈希；否则描述降级方式。
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", "unreadable: %s" % e
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            if size <= limit:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
                return h.hexdigest(), ""
            # 超限：只哈希前缀，并把字节数并入，保证同一文件重跑哈希一致
            h.update(fh.read(limit))
            h.update(b"|truncated|%d" % size)
            return h.hexdigest(), "prefix_hash(limited_to_%d_of_%d)" % (limit, size)
    except OSError as e:
        return "", "unreadable: %s" % e


def read_text_robust(path, limit=MAX_FILE_BYTES):
    """utf-8-sig 优先，逐级降级 gb18030/latin-1。返回 (text, encoding, error)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, "unreadable: %s" % e
    if size > limit:
        return "", None, "file_too_large(%d>%d)" % (size, limit)
    last = ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except UnicodeDecodeError as e:
            last = "%s: %s" % (enc, e)
        except OSError as e:
            return "", None, "unreadable: %s" % e
    return "", None, "undecodable: " + last


def load_json_robust(path):
    """读取 JSON；坏 JSON 不抛异常，返回 (obj, error)。"""
    text, _enc, err = read_text_robust(path)
    if err:
        return {}, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return {}, "bad_json: %s" % e


# --------------------------------------------------------------------------- #
# 原子写盘
# --------------------------------------------------------------------------- #
def atomic_write_text(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_",
                               suffix=os.path.splitext(path)[1])
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return path


# --------------------------------------------------------------------------- #
# 输入发现（先发现，再假设）
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    """扫描输入目录，返回 (files, degradations)。不假设固定文件名/列名。"""
    files = {"case_json": None, "task_md": None, "materials": [], "data": []}
    deg = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing: %s" % input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            base = name.lower()
            try:
                size = os.path.getsize(p)
            except OSError as e:
                deg.append("unreadable: %s (%s)" % (rel, e))
                continue
            if base == "case.json":
                files["case_json"] = p
            elif base == "task.md":
                files["task_md"] = p
            elif base.endswith(DATA_EXTS):
                files["data"].append(p)
            elif base.endswith(TEXT_EXTS):
                files["materials"].append(p)
            if size > MAX_FILE_BYTES and base.endswith(TEXT_EXTS):
                deg.append("file_too_large(skipped): %s (%d bytes)" % (rel, size))
    return files, deg


def build_case_id(files):
    """确定性 case_id：优先取 case.json 的 case_id/id/challenge_id。

    否则用输入目录名 + 关键文件内容哈希派生，保证同一输入重跑一致。
    """
    if files.get("case_json"):
        obj, err = load_json_robust(files["case_json"])
        if not err and isinstance(obj, dict):
            for k in ("case_id", "id", "challenge_id"):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    h = hashlib.sha256()
    for key in ("case_json", "task_md"):
        p = files.get(key)
        if p:
            digest, _ = file_sha256(p)
            h.update((key + ":" + digest).encode("utf-8"))
    base = os.path.basename(files.get("case_json") or "") or "input"
    tag = os.path.splitext(base)[0] or base
    return "%s-%s" % (tag, h.hexdigest()[:16])


# --------------------------------------------------------------------------- #
# 契约提取：显式键优先，正文表格反推兜底
# --------------------------------------------------------------------------- #
def _normalize(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _leaf_template(obj):
    """把契约对象转成回填模板：dict 递归，list 变 []，标量变 None。"""
    if isinstance(obj, dict):
        return {k: _leaf_template(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return []
    return None


def _find_explicit_contract(obj):
    """在 JSON 对象中查找带 contract/schema 语义的值，返回 (template, path)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _normalize(k) in (_normalize(x) for x in CONTRACT_KEYS):
                if isinstance(v, dict):
                    return _leaf_template(v), k
            if isinstance(v, (dict, list)):
                tpl, p = _find_explicit_contract(v)
                if tpl is not None:
                    return tpl, ("%s.%s" % (k, p) if p else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            tpl, p = _find_explicit_contract(v)
            if tpl is not None:
                return tpl, "[%d].%s" % (i, p)
    return None, None


PROSE_CONTRACT = {
    "analysis_result": {
        "cleaning_table": None,
        "statistics": None,
        "figures": [],
        "code": [],
        "query_results": None,
    },
    "quality_evidence": {
        "snapshots": [],
        "rules": [],
        "logs": [],
        "code_and_versions": [],
    },
    "anomalies": {
        "missing": [],
        "outliers": [],
        "failed_queries": [],
        "unexplained": [],
        "human_confirmation": [],
    },
}

PROSE_MARKERS = ("分析结果", "质量与复现证据", "异常说明")


def extract_contract(files):
    """返回 (template, source, notes)。

    source: "explicit"（JSON 键）、"prose"（正文表格反推）、"none"（兜底）。
    notes 记录契约来源与需要人工确认之处。
    """
    notes = []
    obj = {}
    if files.get("case_json"):
        obj, err = load_json_robust(files["case_json"])
        if err:
            notes.append("case.json 读取失败: %s" % err)
    # 1) 显式键优先
    tpl, path = _find_explicit_contract(obj)
    if tpl:
        return tpl, "explicit", notes + ["契约取自 case.json 的 %s" % (path or "contract")]
    # 2) 正文表格反推（题面常见：output_contract 只写在 task.md 表格里）
    text = ""
    for key in ("task_md", "case_json"):
        p = files.get(key)
        if p:
            t, _e, _err = read_text_robust(p)
            if t:
                text += "\n" + t
    hits = [m for m in PROSE_MARKERS if m in text]
    if len(hits) >= 2:
        return (dict(PROSE_CONTRACT), "prose",
                notes + ["契约由正文字段反推: %s；若题面另有显式键请覆盖" %
                         "、".join(hits)])
    # 3) 兜底：最小契约（仅 analysis_result）
    return ({"analysis_result": dict(PROSE_CONTRACT["analysis_result"])},
            "none", notes + ["未找到显式契约，使用最小契约兜底，标记 pending_human"])


# --------------------------------------------------------------------------- #
# result.json 骨架 / 更新
# --------------------------------------------------------------------------- #
def new_result_skeleton(case_id, contract_template, source, notes):
    res = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": utcnow_iso(), "finished_at": None,
                    "seconds": None},
        "artifacts": [],
    }
    conflicts = []
    for k, v in (contract_template or {}).items():
        if k in RESERVED_KEYS:
            conflicts.append(k)
            continue
        res[k] = v
    for n in notes:
        if n not in res["uncertainty"]:
            res["uncertainty"].append(n)
    if conflicts:
        res["uncertainty"].append(
            "契约字段与保留键同名，按本 Skill 语义维护: %s" % ",".join(conflicts))
    if source == "none":
        res["pending_human"].append(
            "未找到显式 output_contract，需人工确认最终字段集")
    return res


def append_change(res, step, action, files=None, seq=None):
    entry = {
        "ts": utcnow_iso(),
        "seq": seq if seq is not None else len(res.get("change_log", [])) + 1,
        "step": step,
        "action": action,
        "files": files or [],
    }
    res.setdefault("change_log", []).append(entry)
    return entry


def parse_json_value(v):
    v = (v or "").strip()
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def deep_merge(base, extra):
    for k, v in extra.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def set_path(obj, dotted, value):
    parts = [p for p in dotted.split(".") if p]
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return obj


def make_source(locator, quote, kind="value"):
    """构造带哈希的证据项。locator 形如 data.csv#L12 或 materials/paper.md#p3。"""
    q = quote if isinstance(quote, str) else str(quote)
    return {"locator": locator, "quote": q, "kind": kind,
            "hash": sha256_bytes(q.encode("utf-8"))[:16]}


# --------------------------------------------------------------------------- #
# 自检（check）
# --------------------------------------------------------------------------- #
def _png_dpi(path):
    """读取 PNG pHYs 块得到物理 dpi；失败返回 (dpi_or_None, error)。"""
    try:
        with open(path, "rb") as fh:
            if fh.read(8) != b"\x89PNG\r\n\x1a\n":
                return None, "not_png"
            while True:
                lenb = fh.read(4)
                if len(lenb) < 4:
                    return None, "truncated"
                length = int.from_bytes(lenb, "big")
                ctype = fh.read(4)
                data = fh.read(length)
                fh.read(4)  # CRC
                if ctype == b"pHYs" and length >= 9:
                    ppu_x = int.from_bytes(data[0:4], "big")
                    unit = data[8]
                    if unit == 1 and ppu_x > 0:
                        return round(ppu_x * 0.0254, 1), None
                    return None, "unit_not_meter"
                if ctype == b"IEND":
                    break
        return None, "no_phys"
    except OSError as e:
        return None, "unreadable: %s" % e


def check_artifact(path):
    """校验单个产物格式，返回 (level, code, message)。level ∈ FAIL/WARN/PASS。"""
    if not path or not os.path.exists(path):
        return "FAIL", "artifact_missing", "产物不存在: %s" % path
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".svg":
            ET.parse(path)
            return "PASS", "svg_ok", path
        if ext == ".pdf":
            with open(path, "rb") as fh:
                head = fh.read(5)
            if head.startswith(b"%PDF-"):
                return "PASS", "pdf_ok", path
            return "FAIL", "bad_pdf", "%s 非 PDF 文件" % path
        if ext == ".png":
            dpi, err = _png_dpi(path)
            if err == "not_png":
                return "FAIL", "bad_png", "%s 非 PNG 文件" % path
            if dpi is not None and dpi < 300:
                return "WARN", "png_low_dpi", "%s dpi=%.1f<300" % (path, dpi)
            return "PASS", "png_ok", "%s dpi=%s" % (path, dpi)
        if ext in (".csv", ".json", ".md", ".txt", ".tsv"):
            text, _enc, err = read_text_robust(path)
            if err:
                return "FAIL", "unreadable_text", "%s: %s" % (path, err)
            return "PASS", "text_ok", path
    except Exception as e:  # 自检永不崩溃
        return "WARN", "check_error", "%s: %s" % (path, e)
    return "PASS", "artifact_ok", path


def check_result(res, output_dir, require_contract=False, contract_template=None):
    """校验 result.json 结构与产物。返回 (findings, pass_count, fail_count)。

    自检永不抛异常；任何读取失败都转成 findings 里的 FAIL/WARN。
    """
    findings = []
    fail = warn = 0
    if not isinstance(res, dict):
        findings.append(("FAIL", "not_dict", "result 根对象不是 dict"))
        return findings, 0, 1
    if not isinstance(res.get("case_id"), str) or not res["case_id"]:
        findings.append(("FAIL", "no_case_id", "缺少 case_id"))
        fail += 1
    if res.get("status") not in STATUS_VALUES:
        findings.append(("FAIL", "bad_status", "status 不在 %s" % (STATUS_VALUES,)))
        fail += 1
    for key in ("sources", "uncertainty", "change_log", "errors",
                "degradations", "artifacts"):
        if not isinstance(res.get(key), list):
            findings.append(("FAIL", "missing_%s" % key, "缺少列表字段 %s" % key))
            fail += 1
    if not isinstance(res.get("pending_human"), list):
        findings.append(("WARN", "no_pending_human", "缺少 pending_human 列表"))
        warn += 1
    if require_contract and isinstance(contract_template, dict):
        for k in contract_template:
            if k in RESERVED_KEYS:
                continue
            if k not in res:
                findings.append(("FAIL", "contract_key_missing",
                                 "契约字段 %s 未回填" % k))
                fail += 1
    for src in res.get("sources", []):
        if not (isinstance(src, dict) and src.get("locator") and src.get("quote")):
            findings.append(("FAIL", "bad_source",
                             "sources 项缺 locator/quote"))
            fail += 1
            break
    for art in res.get("artifacts", []):
        p = art.get("path") if isinstance(art, dict) else art
        if p and not os.path.isabs(p):
            p = os.path.join(output_dir, p)
        level, code, msg = check_artifact(p)
        findings.append((level, code, msg))
        if level == "FAIL":
            fail += 1
        elif level == "WARN":
            warn += 1
    return findings, len([f for f in findings if f[0] == "PASS"]), fail


def write_manifest(files, seed, work_dir):
    """写 work/manifest.json：输入哈希 + 随机种子 + 文件清单，供重跑比对。"""
    manifest = {"seed": seed, "python": sys.version.split()[0],
                "inputs": []}
    for key in ("case_json", "task_md"):
        p = files.get(key)
        if p:
            digest, note = file_sha256(p)
            manifest["inputs"].append({"role": key, "path": p,
                                       "sha256": digest, "note": note})
    for p in sorted(files.get("materials", [])):
        digest, note = file_sha256(p)
        manifest["inputs"].append({"role": "material", "path": p,
                                   "sha256": digest, "note": note})
    try:
        atomic_write_json(os.path.join(work_dir, "manifest.json"), manifest)
    except OSError:
        pass
    return manifest
