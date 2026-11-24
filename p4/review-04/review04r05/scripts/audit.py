# -*- coding: utf-8 -*-
"""audit.py — 论文逻辑与论证审查引擎（纯标准库，无第三方依赖）。

职责：
  1. 发现输入、抽取文本、切分带定位符（p页 ¶P段落）的语料；
  2. 读取 case.json 并原样回填 output_contract 的全部键（递归对齐、保留未知键、
     缺值给 null/[]，并把每个键的对齐状态写进 result.json 的 contract 台账）；
  3. 生成/增量更新/校验 result.json 与 report.md（change_log 只增不改）；
  4. 提供 selfcheck / selftest 的确定性校验。

确定性约定：不使用随机数、不依赖文件系统遍历顺序（统一 sorted），
两次相同输入的输出内容一致（时间戳与运行时计量属挥发性元数据，见 canonical()）。
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
RESULT_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
COVERAGE_DIMS = (
    "factual_verification",   # 事实核验
    "citation_support",       # 引用支持
    "method_design",          # 方法设计
    "statistical_reporting",  # 统计报告
    "argument_chain",         # 论证链
    "academic_norms",         # 学术规范
)
# 顶层信封键（不可被 output_contract 的同名标量覆盖，dict 会递归合并）
ENVELOPE_KEYS = {
    "case_id", "status", "task", "artifacts", "review", "sources",
    "uncertainty", "pending_review", "pending_human", "limitations",
    "errors", "degradations", "change_log", "runtime", "contract",
}
MAX_FILE_BYTES = 2_000_000
MAX_PARA_CHARS = 2_000
FINDING_REQUIRED = (
    "id", "type", "severity", "locator", "quote", "claim",
    "evidence", "reasoning", "suggestion", "status", "confidence",
)

# ---------------------------------------------------------------------------
# 基础 I/O（容错）
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_bytes_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    p = Path(path)
    r: Dict[str, Any] = {"path": str(p), "text": "", "chars": 0,
                         "error": None, "encoding": None}
    if not p.is_file():
        r["error"] = "missing"
        return r
    try:
        size = p.stat().st_size
    except OSError as e:
        r["error"] = "stat:%s" % e.__class__.__name__
        return r
    if size > max_bytes:
        r["error"] = "too_large(%d>%d)" % (size, max_bytes)
        return r
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = None
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            r["encoding"] = enc
            break
        except Exception:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
        r["encoding"] = "utf-8(replace)"
        r["error"] = "decode_replace"
    r["text"] = text
    r["chars"] = len(text)
    return r


def read_json_tolerant(path: str) -> Tuple[Any, Optional[str]]:
    """返回 (data, error)。坏 JSON / 坏编码都不抛异常。"""
    if not Path(path).is_file():
        return {}, "missing"
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    candidates = [raw.decode("utf-8", errors="replace")]
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            candidates.append(raw.decode(enc))
        except Exception:
            pass
    last_err = None
    for text in candidates:
        try:
            data = json.loads(text)
            return data, None
        except Exception as e:
            last_err = str(e)
    return {}, ("json_parse:%s" % (last_err or "unknown"))


def read_json_array(path: str) -> Tuple[List[Any], Optional[str]]:
    data, err = read_json_tolerant(path)
    if err:
        return [], err
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        # 容忍单键包裹（{"findings":[...]} 之类）
        for v in data.values():
            if isinstance(v, list):
                return v, None
        return [], "expected_json_array"
    return [], "expected_json_array"


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------
def discover_input(input_dir: str) -> Dict[str, Any]:
    root = Path(input_dir)
    d: Dict[str, Any] = {"root": str(root), "case_json": None, "task_md": None,
                         "materials": [], "others": [], "error": None}
    if not root.is_dir():
        d["error"] = "input_dir_missing"
        return d
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name == "case.json":
            d["case_json"] = str(p)
        elif name == "task.md":
            d["task_md"] = str(p)
        elif name in ("resources.yaml", "resources.yml"):
            d["others"].append(str(p))
        else:
            d["materials"].append(str(p))
    return d


# ---------------------------------------------------------------------------
# 文本抽取与语料切分
# ---------------------------------------------------------------------------
def _pdftotext(path: str) -> Tuple[str, Optional[str]]:
    try:
        r = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", "-layout", str(path), "-"],
            capture_output=True, timeout=120)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", errors="replace"), None
        err = (r.stderr or b"").decode("utf-8", errors="replace").strip()
        return "", ("exit=%d %s" % (r.returncode, err[:200]))
    except FileNotFoundError:
        return "", "pdftotext_not_found"
    except subprocess.TimeoutExpired:
        return "", "pdftotext_timeout"
    except Exception as e:
        return "", ("pdftotext_error:%s" % e.__class__.__name__)


def _chunk(text: str, limit: int) -> List[str]:
    """把超长段落按句末边界切块，尽量不切断句子。"""
    out: List[str] = []
    while len(text) > limit:
        cut = text.rfind("。", 0, limit)
        if cut < int(limit * 0.5):
            cut = text.rfind(". ", 0, limit)
        if cut < int(limit * 0.5):
            cut = text.rfind(" ", 0, limit)
        if cut < int(limit * 0.5):
            cut = limit
        else:
            cut += 1
        out.append(text[:cut].strip())
        text = text[cut:].lstrip()
    if text.strip():
        out.append(text.strip())
    return out


def split_paras(text: str) -> List[str]:
    """按空行与标题行切分；无空行时也把标题行独立成段，正文连续行归并为一段。

    PDF 硬换行的正文行（非标题、无空行）会被归并，避免句子被逐行切碎。
    """
    paras: List[str] = []
    buf: List[str] = []

    def flush() -> None:
        block = "\n".join(buf).strip()
        if not block:
            buf.clear()
            return
        if len(block) <= MAX_PARA_CHARS:
            paras.append(block)
        else:
            paras.extend(_chunk(block, MAX_PARA_CHARS))
        buf.clear()

    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        s = line.strip()
        if not s:
            flush()
            continue
        if guess_section(s):
            flush()
            paras.append(s)
            continue
        buf.append(line)
        if sum(len(x) for x in buf) > MAX_PARA_CHARS:
            flush()
    flush()
    return paras


_HEAD_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[\.、)）]?\s+(.+)$")


def guess_section(para: str) -> str:
    s = para.strip()
    if not s:
        return ""
    if len(s) <= 120:
        if s.startswith("#"):
            return s.lstrip("#").strip()
        if _HEAD_RE.match(s):
            return s
        if s.isascii():
            if s.isupper() and len(s) >= 3:
                return s
            if re.match(r"^[A-Za-z][A-Za-z0-9 ,\-&:()]{2,80}$", s) \
                    and not s.endswith((".", ",", ":", ";")):
                return s
    return ""


def build_corpus(material_srcs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for src in material_srcs:
        path = src["path"]
        ext = src.get("ext", "")
        text = ""
        error = None
        n_pages = None
        if ext == ".pdf":
            text, error = _pdftotext(path)
            src["error"] = error
            if error is None:
                n_pages = text.count("\f") + 1 if text else 0
        else:
            r = read_bytes_safe(path)
            text = r["text"]
            error = r["error"]
            src["error"] = error
        src["chars"] = len(text) if text else 0
        src["n_pages"] = n_pages
        src["text"] = text
        if error:
            continue
        pages = text.split("\f")
        pid = 0
        current_section = ""
        for pi, page_text in enumerate(pages, 1):
            for para in split_paras(page_text):
                pid += 1
                sec = guess_section(para)
                if sec:
                    current_section = sec
                if ext == ".pdf":
                    locator = "p%d ¶P%04d" % (pi, pid)
                else:
                    locator = "¶P%04d" % pid
                records.append({
                    "src": path,
                    "page": pi if ext == ".pdf" else None,
                    "pid": pid,
                    "locator": locator,
                    "section": current_section or "(无标题)",
                    "text": para,
                })
    return records


def build_section_map(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for rec in records:
        key = rec["section"] or "(无标题)"
        if key not in groups:
            groups[key] = {"section": key, "paras": [], "pages": []}
            order.append(key)
        groups[key]["paras"].append(rec["locator"].split("¶")[-1])
        if rec.get("page"):
            groups[key]["pages"].append(rec["page"])
    out = []
    for key in order:
        g = groups[key]
        g["pages"] = sorted(set(g["pages"]))
        out.append(g)
    return out


# ---------------------------------------------------------------------------
# 任务元信息与 case_id
# ---------------------------------------------------------------------------
def load_task_meta(discovered: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"challenge_id": None, "task_title": None,
                            "case_json_parse_error": None}
    if discovered.get("case_json"):
        data, err = read_json_tolerant(discovered["case_json"])
        meta["case_json_parse_error"] = err
        if isinstance(data, dict):
            meta["challenge_id"] = (data.get("challenge_id") or data.get("challengeId")
                                    or data.get("id") or None)
            meta["task_title"] = data.get("title") or data.get("task_title")
    if discovered.get("task_md"):
        r = read_bytes_safe(discovered["task_md"], max_bytes=200_000)
        if not meta.get("task_title"):
            for line in r["text"].splitlines()[:40]:
                ls = line.strip()
                if ls.startswith("#") and ls.lstrip("#").strip():
                    meta["task_title"] = ls.lstrip("#").strip()
                    break
        if not meta.get("challenge_id"):
            m = re.search(r"challenge[_-]?id[`\s:：]+([A-Za-z0-9\-_.]+)", r["text"], re.I)
            if m:
                meta["challenge_id"] = m.group(1)
    return meta


def derive_case_id(discovered: Dict[str, Any], meta: Dict[str, Any]) -> str:
    if discovered.get("case_json"):
        data, _ = read_json_tolerant(discovered["case_json"])
        if isinstance(data, dict):
            for key in ("case_id", "caseId", "id"):
                v = data.get(key)
                if isinstance(v, str) and v:
                    return v
        return Path(discovered["case_json"]).stem
    return "unknown_case"


def load_output_contract(discovered: Dict[str, Any]) -> Dict[str, Any]:
    if not discovered.get("case_json"):
        return {}
    data, _ = read_json_tolerant(discovered["case_json"])
    c = data.get("output_contract") if isinstance(data, dict) else None
    if isinstance(c, dict):
        return dict(c)
    return {}


# ---------------------------------------------------------------------------
# output_contract 对齐（本包的差异化核心）
# ---------------------------------------------------------------------------
def flatten_keys(obj: Any, prefix: str = "") -> List[str]:
    out: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = (prefix + "." + k) if prefix else k
            out.append(path)
            if isinstance(v, dict):
                out.extend(flatten_keys(v, path))
    return out


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in extra.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = deepcopy(v)
    return base


def get_by_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def has_path(obj: Any, path: str) -> bool:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False
    return True


def merge_contract(envelope: Dict[str, Any], contract: Dict[str, Any],
                   source: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"source": source, "n_keys": 0, "keys": [],
                            "unfilled": [], "collisions": []}

    def walk(obj: Dict[str, Any], prefix: str) -> None:
        for k, v in obj.items():
            path = (prefix + "." + k) if prefix else k
            meta["keys"].append(path)
            if k in envelope and isinstance(envelope[k], dict) and isinstance(v, dict):
                _deep_merge(envelope[k], v)
            elif k in envelope and isinstance(envelope[k], list) and isinstance(v, list):
                if not envelope[k]:
                    envelope[k] = deepcopy(v)
                elif v:
                    meta["collisions"].append(path)
            elif k in envelope and envelope[k] not in (None, "", [], {}):
                meta["collisions"].append(path)
            else:
                envelope[k] = deepcopy(v)
            if isinstance(v, dict):
                walk(v, path)

    walk(contract, "")
    meta["n_keys"] = len(meta["keys"])
    for path in meta["keys"]:
        v = get_by_path(envelope, path)
        if v in (None, "", [], {}):
            meta["unfilled"].append(path)
    return meta


def default_review_block() -> Dict[str, Any]:
    return {
        "summary": "",
        "findings": [],
        "claims": [],
        "commitments": [],
        "coverage": {d: {"status": "pending", "checked": 0, "note": ""}
                     for d in COVERAGE_DIMS},
        "section_map": [],
    }


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------
def scaffold(input_dir: str, output_dir: str, work_dir: str,
             force: bool = False) -> Tuple[Dict[str, Any], Optional[str]]:
    out = Path(output_dir)
    work = Path(work_dir)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    result_path = out / "result.json"
    if result_path.exists() and not force:
        data, err = read_json_tolerant(str(result_path))
        if err is None and isinstance(data, dict) and data.get("case_id"):
            return data, "exists_skipped(use --force to overwrite)"

    discovered = discover_input(input_dir)
    sources: List[Dict[str, Any]] = []
    if discovered.get("case_json"):
        r = read_bytes_safe(discovered["case_json"], max_bytes=500_000)
        sources.append({"path": discovered["case_json"], "kind": "case",
                        "n_pages": None, "chars": r["chars"], "error": r["error"]})
    if discovered.get("task_md"):
        r = read_bytes_safe(discovered["task_md"], max_bytes=500_000)
        sources.append({"path": discovered["task_md"], "kind": "task",
                        "n_pages": None, "chars": r["chars"], "error": r["error"]})
    material_srcs = []
    for p in discovered.get("materials", []):
        ext = Path(p).suffix.lower()
        material_srcs.append({"path": p, "kind": "material", "n_pages": None,
                              "chars": 0, "error": None, "ext": ext})
    records = build_corpus(material_srcs)
    sources.extend([{k: v for k, v in s.items() if k not in ("text", "ext")}
                    for s in material_srcs])

    meta = load_task_meta(discovered)
    case_id = derive_case_id(discovered, meta)
    contract = load_output_contract(discovered)

    # 状态：输入目录缺失 -> input_error；无材料 -> insufficient_evidence；否则 partial
    if discovered.get("error"):
        status = "input_error"
    elif not discovered.get("materials"):
        status = "insufficient_evidence"
    elif not records:
        status = "insufficient_evidence"
    else:
        status = "partial"

    envelope: Dict[str, Any] = {
        "case_id": case_id,
        "status": status,
        "task": {"challenge_id": meta.get("challenge_id"),
                 "task_title": meta.get("task_title")},
        "artifacts": [
            {"path": "result.json", "kind": "structured_result",
             "role": "machine_readable"},
            {"path": "report.md", "kind": "review_report_markdown",
             "role": "human_readable"},
        ],
        "review": default_review_block(),
        "sources": sources,
        "uncertainty": [],
        "pending_review": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"seconds_elapsed": 0.0, "model_requests_used": 0,
                    "budget": {"requests": 64, "seconds": 900}},
    }
    envelope["review"]["section_map"] = build_section_map(records)

    contract_meta: Dict[str, Any]
    if contract:
        contract_meta = merge_contract(envelope, contract, "case.json.output_contract")
    else:
        contract_meta = {"source": "default_schema", "n_keys": 0, "keys": [],
                         "unfilled": [], "collisions": []}
        contract_meta["keys"] = flatten_keys(default_review_block(), "review")
        contract_meta["n_keys"] = len(contract_meta["keys"])
    envelope["contract"] = contract_meta

    if meta.get("case_json_parse_error"):
        envelope["errors"].append({"kind": "case_json_parse",
                                   "msg": meta["case_json_parse_error"]})
    for s in sources:
        if s.get("error"):
            kind = s.get("kind", "material")
            envelope["degradations"].append(
                {"kind": "extract_%s" % kind, "path": s["path"], "msg": s["error"]})
    if not records and status == "insufficient_evidence":
        envelope["limitations"].append(
            {"scope": "corpus", "note": "无可审查文本：材料缺失或全部抽取失败，"
             "六类检查项均无法执行，不编造结论。"})
    envelope["change_log"].append({"ts": now_iso(), "seq": 1,
                                   "event": "skeleton_written",
                                   "note": "合法 partial 骨架已落盘（含 contract 对齐台账）",
                                   "status_after": status})

    write_result(envelope, result_path)
    write_corpus(records, sources, work / "corpus.md")
    meta_payload = {"case_id": case_id, "challenge_id": meta.get("challenge_id"),
                    "task_title": meta.get("task_title"), "status": status,
                    "contract": contract_meta, "n_records": len(records),
                    "discovered": discovered}
    (work / "meta.json").write_text(
        json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    render_report(envelope, out / "report.md")
    return envelope, None


# ---------------------------------------------------------------------------
# 写盘（原子替换）
# ---------------------------------------------------------------------------
def write_result(res: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(res, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_corpus(records: List[Dict[str, Any]], sources: List[Dict[str, Any]],
                 path: Path) -> None:
    lines = ["# 语料库（逐段审查，locator 可直接引用）", ""]
    lines.append("来源 %d 个，段落 %d 个。定位符格式：`p<页> ¶P<段落>`；非 PDF 省略 p 部分。" %
                 (len(sources), len(records)))
    lines.append("引用原文必须逐字复制，不得改写。")
    lines.append("")
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        by_src.setdefault(rec["src"], []).append(rec)
    for src in sources:
        recs = by_src.get(src["path"], [])
        lines.append("")
        lines.append("## 来源: %s" % src["path"])
        lines.append("- kind=%s n_pages=%s chars=%s error=%s" %
                     (src.get("kind"), src.get("n_pages"), src.get("chars"),
                      src.get("error")))
        lines.append("")
        for rec in recs:
            lines.append("### %s [%s]" % (rec["locator"], rec["section"] or "正文"))
            lines.append(rec["text"])
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# report.md 渲染（与 result.json 同步，确定性）
# ---------------------------------------------------------------------------
def render_report(res: Dict[str, Any], path: Path) -> None:
    rv = res.get("review", {})
    cov = rv.get("coverage", {})
    L: List[str] = []
    L.append("# 论文逻辑与论证审查报告")
    L.append("")
    L.append("- case_id: %s" % res.get("case_id"))
    L.append("- 状态: %s" % res.get("status"))
    L.append("- 说明: 本文件由 result.json 确定性渲染，与结构化结果同步。")
    L.append("")
    L.append("## 0. 总评")
    L.append("")
    L.append(rv.get("summary") or "（待补充：一段话总评）")
    L.append("")
    L.append("## 1. 问题清单（findings）")
    L.append("")
    findings = rv.get("findings", [])
    if not findings:
        L.append("（暂无；骨架阶段占位，后续增量写入。）")
    for f in findings:
        L.append("### %s · %s · %s" %
                 (f.get("id", "?"), f.get("type", "?"), f.get("severity", "?")))
        L.append("- 定位: %s" % f.get("locator", ""))
        L.append("- 原文: %s" % f.get("quote", ""))
        L.append("- 主张: %s" % f.get("claim", ""))
        L.append("- 依据: %s" % f.get("evidence", ""))
        L.append("- 推理: %s" % f.get("reasoning", ""))
        L.append("- 建议: %s" % f.get("suggestion", ""))
        L.append("- 状态/置信: %s / %s" % (f.get("status", ""), f.get("confidence", "")))
        L.append("")
    L.append("## 2. 主张与承诺")
    L.append("")
    L.append("claims %d 条；commitments %d 条。" %
             (len(rv.get("claims", [])), len(rv.get("commitments", []))))
    L.append("")
    L.append("## 3. 六类检查项覆盖")
    L.append("")
    L.append("| 维度 | 状态 | 检查数 | 说明 |")
    L.append("|---|---|---|---|")
    for d in COVERAGE_DIMS:
        c = cov.get(d, {})
        L.append("| %s | %s | %s | %s |" %
                 (d, c.get("status", "pending"), c.get("checked", 0), c.get("note", "")))
    L.append("")
    L.append("## 4. 输出契约对齐（contract）")
    L.append("")
    cmeta = res.get("contract", {})
    L.append("- 来源: %s；键数 %d；未填 %d；冲突 %d" %
             (cmeta.get("source"), cmeta.get("n_keys", 0),
              len(cmeta.get("unfilled", [])), len(cmeta.get("collisions", []))))
    if cmeta.get("unfilled"):
        L.append("- 未填键: %s" % "、".join(cmeta["unfilled"]))
    L.append("")
    L.append("## 5. 待复核项与限制")
    L.append("")
    for sec, key in (("uncertainty", "uncertainty"), ("pending_review", "pending_review"),
                     ("pending_human", "pending_human"), ("limitations", "limitations")):
        items = res.get(key, [])
        L.append("- %s (%d):" % (sec, len(items)))
        for it in items:
            if isinstance(it, dict):
                L.append("  - %s" % json.dumps(it, ensure_ascii=False))
            else:
                L.append("  - %s" % str(it))
    L.append("")
    L.append("## 6. 变更记录（change_log）")
    L.append("")
    for e in res.get("change_log", []):
        L.append("- %s [seq=%s] %s: %s" %
                 (e.get("ts", ""), e.get("seq", ""), e.get("event", ""),
                  e.get("note", "")))
    L.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")


# ---------------------------------------------------------------------------
# update（增量写回）
# ---------------------------------------------------------------------------
def next_seq(change_log: List[Dict[str, Any]]) -> int:
    if not change_log:
        return 1
    return max(int(e.get("seq", 0)) for e in change_log) + 1


def update(output_dir: str, findings: Optional[str], claims: Optional[str],
           summary: Optional[str], status: Optional[str]) -> Tuple[Optional[Dict], Optional[str]]:
    result_path = Path(output_dir) / "result.json"
    if not result_path.exists():
        return None, "result.json missing；先运行 scaffold"
    res, err = read_json_tolerant(str(result_path))
    if err or not isinstance(res, dict):
        return None, "result.json 不可解析：%s" % err
    res.setdefault("review", default_review_block())
    res.setdefault("change_log", [])
    res.setdefault("errors", [])
    res.setdefault("uncertainty", [])

    if findings:
        arr, e = read_json_array(findings)
        if e is None:
            res["review"]["findings"] = arr
        else:
            res["errors"].append({"kind": "findings_read", "msg": e})
    if claims:
        arr, e = read_json_array(claims)
        if e is None:
            res["review"]["claims"] = arr
        else:
            res["errors"].append({"kind": "claims_read", "msg": e})
    if summary:
        sp = Path(summary)
        res["review"]["summary"] = sp.read_text(encoding="utf-8") if sp.is_file() else summary
    if status in RESULT_STATUSES:
        res["status"] = status

    # 重新计算 contract 未填键（保持台账与当前内容一致）
    cmeta = res.get("contract")
    if isinstance(cmeta, dict):
        unfilled = [k for k in cmeta.get("keys", [])
                    if get_by_path(res, k) in (None, "", [], {})]
        cmeta["unfilled"] = unfilled
    res["change_log"].append({
        "ts": now_iso(), "seq": next_seq(res["change_log"]),
        "event": "incremental_update", "status_after": res.get("status"),
        "note": "findings=%d claims=%d" %
                (len(res["review"].get("findings", [])),
                 len(res["review"].get("claims", []))),
    })
    write_result(res, result_path)
    render_report(res, Path(output_dir) / "report.md")
    return res, None


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_result(res: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if not isinstance(res.get("case_id"), str) or not res["case_id"]:
        problems.append("case_id 缺失")
    if res.get("status") not in RESULT_STATUSES:
        problems.append("status 非法: %r" % res.get("status"))
    for key in ("task", "review", "sources", "uncertainty", "pending_review",
                "limitations", "errors", "degradations", "change_log", "runtime", "contract"):
        if key not in res:
            problems.append("缺少顶层键: %s" % key)
    cov = res.get("review", {}).get("coverage", {})
    for d in COVERAGE_DIMS:
        if d not in cov:
            problems.append("coverage 缺少维度: %s" % d)
    for f in res.get("review", {}).get("findings", []):
        if not isinstance(f, dict):
            problems.append("findings 含非对象")
            continue
        for k in FINDING_REQUIRED:
            if k not in f:
                problems.append("finding %s 缺字段: %s" % (f.get("id", "?"), k))
    seqs = []
    for e in res.get("change_log", []):
        if not isinstance(e.get("seq"), int):
            problems.append("change_log seq 非整数")
        else:
            seqs.append(e["seq"])
        try:
            datetime.strptime(str(e.get("ts", "")), "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            problems.append("change_log ts 非法: %r" % e.get("ts"))
    if seqs != sorted(set(seqs)):
        problems.append("change_log seq 未严格递增")
    cmeta = res.get("contract", {})
    if isinstance(cmeta, dict) and cmeta.get("keys"):
        for k in cmeta["keys"]:
            if not has_path(res, k):
                problems.append("contract 键未回填: %s" % k)
    if not isinstance(res.get("sources"), list):
        problems.append("sources 应为数组")
    return problems


def selfcheck(output_dir: str) -> bool:
    result_path = Path(output_dir) / "result.json"
    report_path = Path(output_dir) / "report.md"
    problems: List[str] = []
    if not result_path.exists():
        problems.append("result.json 缺失")
    else:
        res, err = read_json_tolerant(str(result_path))
        if err or not isinstance(res, dict):
            problems.append("result.json 不可解析: %s" % err)
        else:
            problems.extend(validate_result(res))
    if not report_path.exists():
        problems.append("report.md 缺失")
    else:
        r = read_bytes_safe(str(report_path))
        if "case_id" not in r["text"] or "findings" not in r["text"]:
            problems.append("report.md 缺关键节")
    if problems:
        for p in problems:
            print("FAIL:", p)
        return False
    print("OK")
    return True


def canonical(res: Dict[str, Any]) -> str:
    """内容确定性比较：剥离时间戳与运行时计量这类挥发性元数据。"""
    c = deepcopy(res)
    for e in c.get("change_log", []):
        if isinstance(e, dict):
            e["ts"] = ""
    rt = c.get("runtime", {})
    if isinstance(rt, dict):
        rt["seconds_elapsed"] = 0.0
        rt["model_requests_used"] = 0
    return json.dumps(c, ensure_ascii=False, sort_keys=True)


def selftest() -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    base = Path(tempfile.mkdtemp(prefix="audit_selftest_"))
    try:
        # 1) 空输入目录
        d1 = base / "empty"; d1.mkdir()
        res1, err1 = scaffold(str(d1), str(base / "o1"), str(base / "w1"), force=True)
        results["empty_input"] = (err1 is None and res1["status"] == "insufficient_evidence"
                                  and bool(res1["case_id"]))

        # 2) 输入目录缺失
        res2, err2 = scaffold(str(base / "nope"), str(base / "o2"), str(base / "w2"), force=True)
        results["missing_input_dir"] = (err2 is None and res2["status"] == "input_error")

        # 3) 坏 JSON case.json
        d3 = base / "badjson"; d3.mkdir()
        (d3 / "case.json").write_text("{ not json", encoding="utf-8")
        (d3 / "task.md").write_text("# T\n\n正文\n", encoding="utf-8")
        (d3 / "paper.txt").write_text("第一段。\n\n第二段。\n", encoding="utf-8")
        res3, err3 = scaffold(str(d3), str(base / "o3"), str(base / "w3"), force=True)
        results["bad_json"] = (err3 is None and res3["status"] in ("partial", "insufficient_evidence")
                               and any(e.get("kind") == "case_json_parse" for e in res3["errors"]))

        # 4) 坏编码文本材料
        d4 = base / "badenc"; d4.mkdir()
        (d4 / "case.json").write_text('{"case_id":"c4"}', encoding="utf-8")
        (d4 / "task.md").write_text("# T\n", encoding="utf-8")
        (d4 / "paper.txt").write_bytes(b"\xff\xfe\x00 bad bytes \x81\x82")
        res4, err4 = scaffold(str(d4), str(base / "o4"), str(base / "w4"), force=True)
        results["bad_encoding"] = (err4 is None and res4["status"] in ("partial", "insufficient_evidence"))

        # 5) 嵌套 output_contract 原样回填
        d5 = base / "contract"; d5.mkdir()
        (d5 / "case.json").write_text(
            json.dumps({"case_id": "c5", "output_contract": {
                "review_report": {"findings": [], "extra": "keep"},
                "custom_flag": None}}), encoding="utf-8")
        (d5 / "task.md").write_text("# T\n", encoding="utf-8")
        (d5 / "paper.txt").write_text("Para one with content.\n\nPara two.\n", encoding="utf-8")
        res5, err5 = scaffold(str(d5), str(base / "o5"), str(base / "w5"), force=True)
        keys5 = res5.get("contract", {}).get("keys", [])
        ok5 = ("review_report" in res5 and "custom_flag" in res5
               and "review_report.findings" in keys5 and "custom_flag" in keys5)
        results["nested_contract"] = (err5 is None and ok5)

        # 6) 确定性：同一输入两次 scaffold 内容一致
        res6a, _ = scaffold(str(d5), str(base / "o6a"), str(base / "w6a"), force=True)
        res6b, _ = scaffold(str(d5), str(base / "o6b"), str(base / "w6b"), force=True)
        results["deterministic"] = (canonical(res6a) == canonical(res6b))

        # 7) update + selfcheck 全链路
        upd, uerr = update(str(base / "o5"), findings=None, claims=None,
                           summary="一段总评", status="partial")
        sc = selfcheck(str(base / "o5"))
        results["update_selfcheck"] = (uerr is None and upd is not None and sc)
    finally:
        pass
    return {"dir": str(base), "results": results}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        out = selftest()
        print(json.dumps(out, ensure_ascii=False, indent=2))
