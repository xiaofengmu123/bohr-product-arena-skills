#!/usr/bin/env python3
"""slides-01 Skill 核心库：输入发现、契约回填、证据校验、页数校验、诚实降级。

仅依赖 Python 标准库（json / zipfile / pathlib / argparse / re / datetime / os / sys）。
设计原则（对应评分维度）：
  - 输入先发现后假设：不假定固定文件名或键名，case.json / task.md / materials/ 均能识别；
  - 输出先落盘后改进：任何时刻都能写出一份合法 result.json，中途异常不留空产物；
  - 证据可溯源：关键结论必须 locator + 逐字 quote，缺失/读不出时显式 not_reported；
  - 失败可解释：errors（硬失败）/ degradations（软降级）/ limitations（材料与软件限制）/
    uncertainty（不确定性）分开记录，绝不把缺失当事实。
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# ---- 路径约定（运行时可被 --input/--work/--output 覆盖） -----------------
DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

VERSION = "1.0.0"
TASK_LABEL = "论文转学术 PPT（slides-01）"

# ---- 状态与字段分类 -----------------------------------------------------
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
VALID_STATUS = {STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR}

# output_contract 常见根字段（本题七字段）
ROOT_FIELDS = [
    "case_id", "status", "editable_source", "export_artifact",
    "content_map", "compliance_checks", "exceptions",
]

# 数组型字段：缺省为 []
LIST_FIELDS = {
    "content_map", "compliance_checks", "exceptions",
    "sources", "uncertainty", "pending_human", "limitations",
    "errors", "degradations", "change_log", "changes",
}
# 对象型字段：缺省为 {}
DICT_FIELDS = {"editable_source", "export_artifact", "runtime"}

# 统一信封（无论 output_contract 是否声明，最终 result.json 都带这些）
ENVELOPE_LIST_FIELDS = [
    "sources", "uncertainty", "pending_human", "limitations",
    "errors", "degradations", "change_log",
]
ENVELOPE_DICT_FIELDS = ["runtime"]

# changes 与 change_log 互为别名：contract 若要求 changes，则回填 change_log 的内容
FIELD_ALIASES = {"changes": "change_log"}

# 证据状态四档（本变体的核心：不确定性与降级显式化）
EVIDENCE_VERIFIED = "verified"        # 找到 locator + 逐字 quote，且内容一致
EVIDENCE_INFERRED = "inferred"        # 有 locator，但为转写/概括，无逐字命中
EVIDENCE_NOT_REPORTED = "not_reported"  # 材料未报告，写入 pending_human / limitations
EVIDENCE_CONFLICTING = "conflicting"  # 多个来源不一致，两处都记录
VALID_EVIDENCE_STATUS = {
    EVIDENCE_VERIFIED, EVIDENCE_INFERRED,
    EVIDENCE_NOT_REPORTED, EVIDENCE_CONFLICTING,
}

# 页角色（slides 常见学术页型）
ROLES = {
    "title", "background", "method", "result", "discussion",
    "limitation", "conclusion", "other",
}

# 单文件大小上限（2MB），超限跳过并记录
MAX_FILE_BYTES = 2 * 1024 * 1024

# PDF 页对象正则：/Type /Page 且不跟 s/S（避免把 /Pages 树算成页）
_PAGE_TYPE_RE = re.compile(rb"/Type\s*/Page(?!s|S)")
# pptx 幻灯片文件名：ppt/slides/slideN.xml，排除 slideLayouts / slideMasters
_SLIDE_NAME_RE = re.compile(r"^ppt/slides/slide\d+\.xml$")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path: Path, max_bytes: int = MAX_FILE_BYTES):
    """读 UTF-8 文本，容错 BOM / 坏编码 / 空文件 / 超限。

    返回 (text|None, error|None)。坏编码不会抛异常，交由调用方记录。
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{Path(path).name} 读取失败: {exc.__class__.__name__}"
    if not raw:
        return None, f"{Path(path).name} 为空文件"
    if len(raw) > max_bytes:
        return None, f"{Path(path).name} 超过 {max_bytes // (1024 * 1024)}MB 上限"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "编码异常，已替换非法字节"


def read_json(path: Path):
    """读 JSON，容错 BOM / 坏编码 / 坏 JSON。返回 (obj|None, error|None)。"""
    text, err = read_text(path)
    if err and text is None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{Path(path).name} JSON 解析失败: {exc}"
    except (TypeError, ValueError) as exc:
        return None, f"{Path(path).name} JSON 解析失败: {exc}"


def write_json(path: Path, obj) -> bool:
    """原子性写 JSON（先写临时文件再替换）。返回是否成功。"""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(path) + ".tmp")
        tmp.write_bytes(
            (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
        tmp.replace(path)
        return True
    except OSError:
        return False


def write_text(path: Path, text: str) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(str(text).encode("utf-8"))
        return True
    except OSError:
        return False


def ensure_dir(path: Path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, ""
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


def discover_materials(input_root: Path):
    """扫描输入目录，识别 case.json / task.md 及其余材料文件。

    返回 (case_path|None, task_path|None, materials:list[dict])。
    不假定固定文件名：优先 exact 匹配，其次大小写不敏感，最后按后缀识别。
    """
    if not input_root.is_dir():
        return None, None, []

    case_path = None
    task_path = None
    materials = []

    # 先按确切文件名定位（case.json / task.md）
    case_candidates = ["case.json", "CASE.json", "Case.json"]
    task_candidates = ["task.md", "TASK.md", "task.txt"]

    for name in case_candidates:
        p = input_root / name
        if p.is_file():
            case_path = p
            break
    for name in task_candidates:
        p = input_root / name
        if p.is_file():
            task_path = p
            break

    # 若 case.json 不在根目录，则递归找（仍优先名为 case.json 的文件）
    if case_path is None:
        for p in input_root.rglob("case.json"):
            case_path = p
            break

    for path in sorted(input_root.rglob("*")):
        if not path.is_file():
            continue
        if case_path is not None and path.resolve() == case_path.resolve():
            continue
        if task_path is not None and path.resolve() == task_path.resolve():
            continue
        rel = path.relative_to(input_root).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        suffix = path.suffix.lower()
        materials.append({
            "path": rel,
            "abs": str(path),
            "size": size,
            "kind": _classify_suffix(suffix, rel),
            "skipped": size > MAX_FILE_BYTES,
        })
    return case_path, task_path, materials


def _classify_suffix(suffix: str, rel: str) -> str:
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".pptx", ".ppt", ".odp"}:
        return "template"
    if suffix in {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}:
        return "text"
    if suffix in {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}:
        return "figure"
    if suffix in {".docx", ".doc", ".xlsx", ".xls"}:
        return "office"
    if suffix in {".bib", ".tex"}:
        return "bibliography"
    if rel.startswith("materials/"):
        return "material"
    return "other"


def contract_keys(case: dict) -> list:
    """从 case.json 的 output_contract 提取字段名（dict 或 list 均可）。

    返回有序、去重的字段名列表；缺失时回退到 ROOT_FIELDS 并标注（由调用方记 degradation）。
    """
    raw = case.get("output_contract") if isinstance(case, dict) else None
    keys: list = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    if isinstance(item.get(cand), str) and item[cand].strip():
                        keys.append(item[cand].strip())
                        break
    ordered, seen = [], set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    if not ordered:
        ordered = list(ROOT_FIELDS)
    return ordered


def default_value(key: str):
    if key in LIST_FIELDS:
        return []
    if key in DICT_FIELDS:
        return {}
    return None


def fill_contract(keys: list, payload: dict):
    """按契约键回填：payload 已有的取 payload，否则取缺省值。

    返回 (filled:dict, missing:list)。别名字段（changes→change_log）一并处理。
    """
    filled, missing = {}, []
    for key in keys:
        src = FIELD_ALIASES.get(key, key)
        if src in payload and payload[src] is not None:
            filled[key] = payload[src]
        else:
            filled[key] = default_value(key)
            missing.append(key)
    return filled, missing


def pptx_slide_count(path) -> int | None:
    """通过 zip 结构统计 pptx 幻灯片数（不依赖第三方库）。

    只匹配 ppt/slides/slideN.xml，排除 layout/master。失败返回 None。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return None
    slides = [n for n in names if _SLIDE_NAME_RE.match(n)]
    return len(slides) if slides else None


def pdf_page_count(path) -> int | None:
    """尽力统计 PDF 页数（无第三方库）。统计 /Type /Page 对象。

    仅对未压缩对象流的 PDF 可靠；找不到页对象时返回 None（由调用方记 uncertainty）。
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data.startswith(b"%PDF"):
        return None
    matches = _PAGE_TYPE_RE.findall(data)
    if matches:
        return len(matches)
    return None


def pdf_is_valid(path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def is_likely_binary_text(text: str) -> bool:
    """判断提取出的文本是否像二进制乱码（用于识别 PDF 加密 / 无文本层）。"""
    if not text:
        return False
    sample = text[:2000]
    weird = sum(1 for ch in sample if ord(ch) < 9 or (14 <= ord(ch) <= 31))
    return weird / max(1, len(sample)) > 0.08


def evidence_item(ev_id: str, locator: str = "", quote: str = "",
                  status: str = EVIDENCE_NOT_REPORTED) -> dict:
    """构造一条规范化的证据条目。"""
    return {
        "id": ev_id,
        "locator": locator,
        "quote": quote,
        "status": status if status in VALID_EVIDENCE_STATUS else EVIDENCE_NOT_REPORTED,
    }


def normalize_evidence(ev) -> dict:
    """把宽松输入的证据条目规范化为统一结构，缺字段补默认值。"""
    if isinstance(ev, str):
        return evidence_item("", "", ev, EVIDENCE_NOT_REPORTED)
    if not isinstance(ev, dict):
        return evidence_item("", "", str(ev), EVIDENCE_NOT_REPORTED)
    status = ev.get("status", EVIDENCE_NOT_REPORTED)
    if status not in VALID_EVIDENCE_STATUS:
        status = EVIDENCE_NOT_REPORTED
    return {
        "id": str(ev.get("id", "")),
        "locator": str(ev.get("locator", "")),
        "quote": str(ev.get("quote", "")),
        "status": status,
    }


def _json_summary(obj) -> str:
    """把命令执行摘要序列化为单行 JSON（stdout 输出，便于 Agent 读取）。"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"error": "summary 序列化失败"}, ensure_ascii=False)


def merge_env_lists(result: dict, draft: dict, key: str):
    """把 draft 里的信封列表字段安全合并进 result（去重，保留顺序）。"""
    vals = draft.get(key)
    if not isinstance(vals, list):
        return
    existing = result.get(key)
    if not isinstance(existing, list):
        existing = []
    for item in vals:
        if item not in existing:
            existing.append(item)
    result[key] = existing
