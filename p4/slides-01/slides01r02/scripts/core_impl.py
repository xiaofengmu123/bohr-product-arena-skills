#!/usr/bin/env python3
"""slides-01 机械脚手架核心：输入发现、契约解析、原子写骨架、产物校验、页数统计。

仅依赖 Python 标准库。内容组织与写作由 Agent 完成，本模块不做任何科学推断。
"""
from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

STATUS_VALUES = {"ok", "partial", "insufficient_evidence", "input_error"}
DEFAULT_CASE_ID = "unknown-case"

# 信封字段：无论 output_contract 是否声明都要写入 result.json
ENVELOPE_LIST_FIELDS = (
    "sources", "uncertainty", "pending_human", "change_log", "changes",
    "errors", "degradations",
)
ENVELOPE_DICT_FIELDS = ("runtime",)

# 按字段名推断默认值类型（仅作骨架占位，Agent 后续覆盖为真实值）
LIST_FIELD_HINTS = (
    "content_map", "compliance_checks", "exceptions", "sources", "uncertainty",
    "pending_human", "change_log", "changes", "errors", "degradations",
)
DICT_FIELD_HINTS = ("editable_source", "export_artifact", "runtime")

KNOWN_SHAPES = {
    "editable_source": {"path": None, "format": None, "slides": None},
    "export_artifact": {"pdf": {"path": None, "pages": None}},
}

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过并记录

EXT_KIND = {
    ".pdf": "pdf", ".pptx": "pptx", ".ppt": "ppt", ".docx": "docx", ".xlsx": "xlsx",
    ".csv": "csv", ".json": "json", ".md": "text", ".txt": "text", ".tex": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".webp": "image", ".svg": "image", ".yaml": "text", ".yml": "text",
    ".xml": "xml", ".zip": "archive",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path: Path, max_bytes: int = MAX_FILE_BYTES):
    """读取 UTF-8 文本（容 BOM）。返回 (text, err)。"""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"{path.name} 读取失败: {exc.__class__.__name__}"
    if size > max_bytes:
        return None, f"{path.name} 超过 {max_bytes} 字节上限，跳过"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"{path.name} 读取失败: {exc.__class__.__name__}"
    if not raw.strip():
        return None, f"{path.name} 为空文件"
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        # 坏编码：给出前若干字节的可见摘要，交由 Agent 用工具重读
        preview = raw[:64].decode("latin-1", errors="replace")
        return None, f"{path.name} 非 UTF-8 文本（latin-1 预览: {preview!r}）"


def read_json(path: Path):
    """读取 JSON，返回 (obj, err)。"""
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{path.name} JSON 解析失败: {exc}"


def atomic_write_text(path: Path, text: str) -> bool:
    """同目录临时文件 + os.replace，保证 result.json 任何时刻都是完整 JSON。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(text.encode("utf-8"))
        tmp.replace(path)
        return True
    except OSError:
        return False


def atomic_write_json(path: Path, obj) -> bool:
    return atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def classify_kind(name: str) -> str:
    lower = name.lower()
    for ext, kind in EXT_KIND.items():
        if lower.endswith(ext):
            return kind
    return "other"


def discover_materials(input_root: Path):
    """扫描输入目录，返回 (materials, degradations)。不假设固定文件名/键名。"""
    materials, degradations = [], []
    if not input_root.is_dir():
        return materials, [f"输入目录不存在: {input_root}"]
    for path in sorted(input_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(input_root).as_posix()
        if rel == "case.json":
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            degradations.append(f"{rel} 超过 {MAX_FILE_BYTES} 字节上限，跳过")
            continue
        materials.append({
            "rel": rel,
            "abs": str(path),
            "size": size,
            "kind": classify_kind(path.name),
        })
    return materials, degradations


def contract_fields(case: dict):
    """从 output_contract 提取有序字段名；缺失时降级并返回默认七字段。"""
    raw = case.get("output_contract") if isinstance(case, dict) else None
    keys, degradations = [], []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    v = item.get(cand)
                    if isinstance(v, str) and v.strip():
                        keys.append(v.strip())
                        break
    if not keys:
        degradations.append("output_contract 缺失或无法解析，降级到默认七字段")
        keys = ["case_id", "status", "editable_source", "export_artifact",
                "content_map", "compliance_checks", "exceptions"]
    ordered, seen = [], set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered, degradations


def default_value(key: str):
    if key in LIST_FIELD_HINTS:
        return []
    if key in DICT_FIELD_HINTS:
        return dict(KNOWN_SHAPES.get(key, {}))
    return None


def build_skeleton(case: dict, fields):
    """构造 result.json 骨架：case_id + 全部契约字段 + 信封字段，status=partial。"""
    case_id = case.get("case_id") if isinstance(case, dict) else None
    if not isinstance(case_id, str) or not case_id.strip():
        case_id = DEFAULT_CASE_ID
    result = {"case_id": case_id, "status": "partial"}
    for key in fields:
        if key not in result:
            result[key] = default_value(key)
    # 信封字段兜底（不覆盖契约已给的键）
    for key in ENVELOPE_LIST_FIELDS:
        if key not in result:
            result[key] = []
    for key in ENVELOPE_DICT_FIELDS:
        if key not in result:
            result[key] = {}
    return result


def pdf_page_count(path: Path):
    """启发式统计 PDF 页数（仅标准库）：数 /Type /Page 对象。无法确定时返回 None。"""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    # /Type /Page 后面不能紧跟 s（排除 /Pages）
    pages = re.findall(rb"/Type\s*/Page(?![sS])", data)
    if pages:
        return len(pages)
    # 回退：取页树 /Count 的最大值（近似）
    counts = [int(c) for c in re.findall(rb"/Count\s+(\d+)", data)]
    return max(counts) if counts else None


def pptx_slide_count(path: Path):
    """统计 pptx 幻灯片页数：数 ppt/slides/slideN.xml。失败返回 None。"""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return None
    slides = [n for n in names if re.match(r"^ppt/slides/slide\d+\.xml$", n)]
    return len(slides) if slides else None


def pptx_inspect(path: Path):
    """检查 pptx 模板：幻灯片数、母版数、版式数、每页文本预览。失败返回 None。

    供 Agent 在 P2 成稿前验证"真继承母版/版式"，以及 P3 自检"非空白+贴文本框"。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            slides = sorted(n for n in names if re.match(r"^ppt/slides/slide\d+\.xml$", n))
            masters = [n for n in names if re.match(r"^ppt/slideMasters/slideMaster\d+\.xml$", n)]
            layouts = [n for n in names if re.match(r"^ppt/slideLayouts/slideLayout\d+\.xml$", n)]
            texts = []
            for name in slides:
                xml = zf.read(name).decode("utf-8", errors="replace")
                runs = re.findall(r"<a:t>(.*?)</a:t>", xml, flags=re.S)
                texts.append(" ".join(runs).strip()[:80])
    except (OSError, zipfile.BadZipFile):
        return None
    return {
        "slides": len(slides),
        "masters": len(masters),
        "layouts": len(layouts),
        "slide_text_preview": texts,
    }


def validate_result(result: dict, fields, output_root: Path, strict: bool = False):
    """校验 result.json。返回 check 列表：{id, severity, message}。"""
    checks = []

    def add(cid, severity, message):
        checks.append({"id": cid, "severity": severity, "message": message})

    if not isinstance(result, dict):
        add("json_root", "error", "result.json 顶层不是对象")
        return checks

    case_id = result.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        add("case_id", "error", "case_id 缺失或为空")
    else:
        add("case_id", "pass", f"case_id = {case_id}")

    status = result.get("status")
    if status not in STATUS_VALUES:
        add("status", "warn" if isinstance(status, str) else "error",
            f"status 非标准值: {status!r}（应为 {sorted(STATUS_VALUES)} 之一）")
    else:
        add("status", "pass", f"status = {status}")

    missing = [k for k in fields if k not in result]
    if missing:
        add("contract_fields", "error", f"output_contract 缺失字段: {missing}")
    else:
        add("contract_fields", "pass", f"output_contract 字段齐全（{len(fields)} 个）")

    # 产物存在性：路径为字符串时检查（骨架阶段可为 null/占位）
    for key, nested in (("editable_source", None), ("export_artifact", ("pdf",))):
        if key not in result or not isinstance(result[key], dict):
            continue
        obj = result[key]
        path_val = obj.get("path") if nested is None else obj.get("pdf", {}).get("path") if isinstance(obj.get("pdf"), dict) else None
        if isinstance(path_val, str) and path_val.strip():
            target = output_root / path_val
            sev = "error" if strict else "warn"
            add(f"{key}_exists", "pass" if target.is_file() else sev,
                f"{key} 指向 {'存在' if target.is_file() else '不存在'}: {path_val}")
        else:
            add(f"{key}_path", "warn", f"{key} 未给出真实路径（骨架/占位）")

    if not isinstance(result.get("change_log"), list):
        add("change_log", "warn", "change_log 不是数组（应为每次重写追加 {time, scope, why}）")

    return checks


def summarize_checks(checks):
    errors = [c for c in checks if c["severity"] == "error"]
    warns = [c for c in checks if c["severity"] == "warn"]
    return errors, warns
