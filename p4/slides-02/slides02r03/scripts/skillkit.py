"""确定性工具库（academic-slide-builder-deterministic 共享核心）。

只使用 Python 标准库。序列化是规范的：sort_keys=True、ensure_ascii=False、
固定 2 空格缩进、末尾换行、临时文件 + os.replace 原子写盘。所有这一切
用于保证：同一输入 + 同一命令序列 => 两次运行得到字节一致的 result.json。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import tempfile

SKILL_NAME = "academic-slide-builder-deterministic"
VERSION = "1.0.0"

# 运行预算与资源契约（与 SKILL.md 一致，供 self_check 引用）。
BUDGET_SECONDS = 900
BUDGET_REQUESTS = 64
SKELETON_DEADLINE_SECONDS = 180
RESPONSE_TOKEN_LIMIT = 16384
MAX_TEXT_FILE_BYTES = 2_000_000
DEFAULT_SEED = 20261123

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_VALUES = (STATUS_COMPLETE, STATUS_PARTIAL, STATUS_FAILED)

STANDARD_FIELDS = (
    "case_id", "status", "presentation_file", "preview_files",
    "speaker_notes", "page_plan", "evidence_map", "version_records",
    "sources", "uncertainty", "pending_human", "change_log",
    "determinism", "errors", "degradations",
)


def skill_root() -> str:
    """Skill 包根目录（scripts/ 的上一级）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def env_dir(name: str, default: str) -> str:
    return os.environ.get(name, default)


def iso_now() -> str:
    """墙钟时间；只用于 work/run_meta.json，明确排除在确定性校验之外。"""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read_text(path: str, cap: int = MAX_TEXT_FILE_BYTES) -> str:
    """读取 UTF-8 文本，容忍 BOM/坏编码；超限返回空串（由调用方记录 skipped）。"""
    if not os.path.exists(path):
        return ""
    if os.path.getsize(path) > cap:
        return ""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def load_json(path: str):
    """返回 (data_or_None, error_or_None)。坏 JSON 永不抛异常，只降级。"""
    if not os.path.exists(path):
        return None, "missing"
    text = read_text(path)
    if text == "":
        return None, "empty_or_oversize"
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 —— 必须降级而非崩溃
        return None, "bad_json: %s: %s" % (exc.__class__.__name__, exc)


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_json_atomic(path: str, obj) -> str:
    """规范序列化 + 原子写盘。返回写入路径。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = canonical_json(obj)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_rel(path: str, base: str) -> str:
    """转正斜杠相对路径；在 base 之外则返回原路径。"""
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        rel = path
    return rel.replace("\\", "/")


def inventory(dir_path: str, cap: int = MAX_TEXT_FILE_BYTES):
    """确定性递归清单（按正斜杠相对路径排序）。"""
    entries = []
    if not os.path.isdir(dir_path):
        return entries
    for root, dirs, files in os.walk(dir_path):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = norm_rel(full, dir_path)
            size = os.path.getsize(full)
            skipped = size > cap
            entry = {"path": rel, "size": size, "skipped": skipped}
            if not skipped:
                entry["sha256"] = sha256_file(full)
            entries.append(entry)
    entries.sort(key=lambda e: e["path"])
    return entries


def input_digest(input_dir: str) -> str:
    """对 /input 目录排序清单做 SHA-256，供评审验证同输入->同产物。"""
    parts = []
    for e in inventory(input_dir):
        parts.append("%s\t%d\t%s" % (e["path"], e["size"], e.get("sha256", "SKIP")))
    return sha256_text("\n".join(parts))
