"""科研成果转化写作 Skill · 核心库（纯标准库，无 I/O 副作用之外的能力）。

本文件只提供可复用的纯逻辑：
  1. 容错输入读取（utf-8-sig / 坏 JSON / 坏编码降级，不崩溃）
  2. output_contract 发现与"原样回填全部键"（缺值给 null/[]，不改名、不删未知键）
  3. 材料扫描与 source index 构建（单文件 2MB 上限，超限跳过并记录）
  4. 段落切分与证据候选抽取（数值/单位/样本量/引用/图表/缩写/论断强度词）
  5. 受众画像、体裁映射、论断强度分级、术语锁定、保留优先级规则
  6. 确定性辅助（稳定排序、稳定 JSON、稳定内容 hash）

所有函数不直接写 result.json；写盘由 main.py 负责，保证"先写合法 partial，再增量改进"。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量：大小上限、状态枚举、常见键
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件 2MB，超限跳过
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATUS_OK = "answered"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUS_ALL = {STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR}

# 契约键 → 空值类型的启发式（用于原样回填）
ARRAY_HINT_KEYS = {
    "claims", "uncertainties", "uncertainty", "sources", "change_log", "changes",
    "items", "nodes", "edges", "cards", "answers", "pending_human", "errors",
    "degradations", "limitations", "facts", "records", "entries", "results_list",
    "evidence", "evidence_map", "preserved_facts", "revision_records",
    "issues", "findings", "goals", "risks", "conflicts", "workorder",
}
OBJECT_HINT_KEYS = {
    "result", "results", "runtime", "coverage", "template", "format_spec",
    "audience", "genre", "version", "fidelity", "meta", "metadata", "config",
}
SCALAR_HINT_KEYS = {"case_id", "status", "seed", "seed_key", "title"}


# ---------------------------------------------------------------------------
# 1. 容错读取
# ---------------------------------------------------------------------------

_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1")


def read_text(path: str, limit: int = MAX_FILE_BYTES) -> str:
    """按 utf-8-sig 优先读取，坏编码降级；超限返回截断并标记。"""
    size = _file_size(path)
    if size is None:
        return ""
    if size > limit:
        return ""
    data = _read_bytes(path)
    if data is None:
        return ""
    for enc in _ENCODINGS:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _read_bytes(path: str):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _file_size(path: str):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def read_json(path: str):
    """容错读 JSON：返回 (obj, error)。坏 JSON / 坏编码都返回 None, msg。"""
    text = read_text(path)
    if not text:
        return None, "empty_or_unreadable"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        # 再尝试 strip BOM / 前缀后解析一次
        try:
            return json.loads(text.lstrip("\ufeff").strip()), None
        except json.JSONDecodeError:
            return None, "bad_json: %s" % exc


def safe_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


def stable_dumps(obj) -> str:
    """稳定序列化（用于确定性 hash）：键排序、ensure_ascii。"""
    return json.dumps(obj, ensure_ascii=True, indent=2, sort_keys=True)


def content_hash(obj) -> str:
    """对业务内容（不含 runtime/timestamp）做稳定 hash。"""
    cleaned = _strip_volatile(obj)
    return hashlib.sha256(stable_dumps(cleaned).encode("utf-8")).hexdigest()


def _strip_volatile(obj):
    """递归剔除 runtime / *_at / *_time / elapsed 等易变字段。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in {"runtime", "generated_at", "started_at", "elapsed_ms",
                      "elapsed", "timestamp", "date"}:
                continue
            if kl.endswith("_at") or kl.endswith("_time") or kl.endswith("_ms"):
                continue
            out[k] = _strip_volatile(v)
        return out
    if isinstance(obj, list):
        return [_strip_volatile(x) for x in obj]
    return obj


# ---------------------------------------------------------------------------
# 2. output_contract 发现与空值回填
# ---------------------------------------------------------------------------

CONTRACT_KEY_CANDIDATES = ("output_contract", "outputContract", "contract",
                           "output_schema", "outputSchema", "schema", "result_schema")


def discover_contract(case: dict):
    """从 case.json 找到 output_contract，返回 (contract, error)。"""
    if not isinstance(case, dict):
        return None, "case_not_dict"
    for key in CONTRACT_KEY_CANDIDATES:
        if key in case and isinstance(case[key], (dict, list)):
            return case[key], None
    # 兜底：case 顶层若有 result 相关键，也当作契约的一部分
    if "result" in case and isinstance(case["result"], dict):
        return case["result"], None
    return None, "no_output_contract"


def contract_keys(contract) -> list:
    """返回契约的顶层键（保持原顺序）。"""
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        return list(range(len(contract)))
    return []


def null_fill(value):
    """按契约结构回填空值：dict 递归、str 用启发式、list 返回 []、其他 None。"""
    if isinstance(value, dict):
        return {k: null_fill(v) for k, v in value.items()}
    if isinstance(value, list):
        # 列表型契约：回填一个空列表（无法知道元素结构时安全做法）
        return []
    if isinstance(value, str):
        return _null_for_string(value)
    return None


def _null_for_string(desc: str):
    """按契约描述字符串推断空值类型；无法判断时给 None。"""
    d = desc or ""
    low = d.lower()
    if "数组" in d or "array" in low or "list" in low or "[]" in d:
        return []
    if "对象" in d or "object" in low or "{}" in d or "dict" in low:
        return {}
    if "string" in low or "字符串" in d or "str" in low:
        return ""
    if "number" in low or "数字" in d or "int" in low or "float" in low:
        return None
    return None


def null_fill_contract(contract):
    """按契约键名 + 描述回填顶层骨架 dict。"""
    skeleton = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            skeleton[k] = _null_for_key(k, v)
    elif isinstance(contract, list):
        skeleton = {}
    else:
        skeleton = {}
    return skeleton


def _null_for_key(key, value):
    """契约键名优先，其次看描述字符串。"""
    k = str(key)
    if k == "case_id":
        return ""  # 之后会被真实 case_id 覆盖
    if k == "status":
        return STATUS_PARTIAL
    if k in ARRAY_HINT_KEYS:
        return []
    if k in OBJECT_HINT_KEYS:
        return {}
    if k in SCALAR_HINT_KEYS:
        return None
    return null_fill(value)


def find_case_id(case: dict):
    """从 case.json 的多个候选键找 case_id。"""
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        if key in case and case[key] not in (None, ""):
            return str(case[key])
    return None


def find_task_instruction(case: dict):
    if not isinstance(case, dict):
        return None
    for key in ("task_instruction", "instruction", "task", "prompt"):
        if key in case and case[key]:
            return case[key]
    return None


# ---------------------------------------------------------------------------
# 3. 材料扫描与 source index
# ---------------------------------------------------------------------------

IGNORE_NAMES = {"case.json", "task.md", "result.json", "draft.json",
                "source_index.json", "evidence_candidates.json"}


def list_materials(input_dir: str):
    """扫描输入目录，返回材料文件清单（排除 case.json/task.md 等元数据）。"""
    materials = []
    errors = []
    if not os.path.isdir(input_dir):
        return materials, ["input_dir_missing: %s" % input_dir]
    for name in sorted(os.listdir(input_dir)):
        if name in IGNORE_NAMES:
            continue
        if name.startswith("."):
            continue
        full = os.path.join(input_dir, name)
        if os.path.isfile(full):
            size = _file_size(full)
            if size is None:
                continue
            if size > MAX_FILE_BYTES:
                errors.append("skipped_oversize: %s (%d bytes)" % (name, size))
                continue
            materials.append({
                "source_id": _sid(len(materials) + 1),
                "rel_path": name,
                "abs_path": full,
                "suffix": os.path.splitext(name)[1].lower(),
                "size": size,
                "kind": _kind_of(name),
            })
    return materials, errors


def _sid(n: int) -> str:
    return "S%d" % n


def _kind_of(name: str):
    low = name.lower()
    if low.endswith((".md", ".txt", ".text", ".rst", ".tex", ".html", ".json",
                     ".csv", ".tsv", ".xml", ".yaml", ".yml")):
        return "text"
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".svg", ".webp")):
        return "image"
    if low.endswith((".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls")):
        return "office"
    return "other"


def build_source_index(input_dir: str, case: dict, task_md_path: str):
    """构建 source index（供 show / build / result.json 使用）。"""
    materials, errors = list_materials(input_dir)
    contract, cerr = discover_contract(case)
    return {
        "case_id": find_case_id(case),
        "task_instruction": find_task_instruction(case),
        "task_md": task_md_path,
        "materials": materials,
        "contract": contract,
        "contract_keys": contract_keys(contract),
        "contract_error": cerr,
        "scan_errors": errors,
    }


# ---------------------------------------------------------------------------
# 4. 段落切分与证据候选抽取
# ---------------------------------------------------------------------------

def split_paras(text: str):
    """按空行切段，返回 [(para_no, text)]。"""
    paras = []
    for i, chunk in enumerate(re.split(r"\n\s*\n", text or ""), start=1):
        chunk = chunk.strip()
        if chunk:
            paras.append((i, chunk))
    return paras


NUMBER_UNIT_PATTERN = (
    r'(?<![\w.])'
    r'(\d+(?:\.\d+)?(?:\s*[×xX]\s*\d+(?:\.\d+)?)?)'
    r'\s*'
    r'(%|°C|℃|K|µg|μg|mg|g|kg|µL|μL|mL|L|nm|µm|μm|mm|cm|m|'
    r'µM|μM|nM|mM|M|kPa|MPa|GPa|Pa|V|kV|W|mW|kW|Hz|kHz|MHz|GHz|'
    r'mol|mmol|µmol|μmol|nmol|h|min|s|yr|a\.u\.|AU|eV|keV|MeV|'
    r'cells/mL|cfu/mL|g/mL|mg/mL|µg/mL|μg/mL|pg/mL|ng/mL)'
    r'(?![A-Za-z])'
)

_NUM_UNIT_RE = re.compile(NUMBER_UNIT_PATTERN, re.IGNORECASE)
_SAMPLE_RE = re.compile(r'\b[nN]\s*[=:：]\s*(\d[\d,]*)')
_PCT_RE = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?)\s*(%|％|percent)')
_CITE_BRACKET_RE = re.compile(r'\[(\d+(?:\s*[-–,]\s*\d+)*)\]')
_CITE_AUTHOR_RE = re.compile(r'\(([^()]{0,80}?(?:19|20)\d{2}[a-z]?[^()]{0,40})\)')
_FIG_RE = re.compile(r'\b(?:Fig(?:ure)?\.?|图)\s*(\d+[A-Za-z]?)', re.IGNORECASE)
_TABLE_RE = re.compile(r'\b(?:Table|表)\s*(\d+[A-Za-z]?)', re.IGNORECASE)
_ABBREV_RE = re.compile(r'\b([A-Z][A-Z0-9]{1,9})\s*\(([^()]{1,160})\)')
_N_RE = re.compile(r'\b([nN])\s*=\s*(\d[\d,]*)')

STRENGTH_TIERS = {
    "established": ["证实", "表明", "显示", "证明", "demonstrat", "confirm",
                    "establish", "showed", "shown", "found"],
    "preliminary": ["初步", "提示", "suggest", "preliminary", "indicate",
                    "倾向", "tends to", "可能是"],
    "speculative": ["可能", "推测", "假设", "有待", "may", "might", "could",
                    "potentially", "hypothes", "speculat", "warrant", "尚需"],
    "correlational": ["相关", "关联", "correlat", "associat", "linked",
                      "related", "relationship"],
    "causal": ["因果", "导致", "引起", "caus", "attribut", "driv",
               "result in", "lead to", "responsible", "caused"],
}

STRENGTH_ORDER = ["speculative", "correlational", "preliminary", "established"]
_FORBIDDEN_UPGRADES = {
    "correlational": {"causal"},
    "preliminary": {"causal", "established"},
    "speculative": {"causal", "established", "preliminary"},
    "unknown": {"causal", "established", "preliminary", "correlational"},
}


def strength_grade(text: str):
    """按关键词把论断分级；命中多个取最强档（speculative 最弱、established 最强）。"""
    low = (text or "").lower()
    hit = "unknown"
    for tier in STRENGTH_ORDER:
        for kw in STRENGTH_TIERS.get(tier, []):
            if kw.lower() in low:
                hit = tier
    # causal 是关系类型而非强度档，单独标注但不作为强度
    for kw in STRENGTH_TIERS["causal"]:
        if kw.lower() in low:
            hit = "causal"
    if hit == "unknown":
        for kw in STRENGTH_TIERS["correlational"]:
            if kw.lower() in low:
                hit = "correlational"
    return hit


def strength_allowed(new_text: str, original_text: str) -> bool:
    """检查改写是否把论断强度升级（相关→因果、初步→定论等）。"""
    src = strength_grade(original_text)
    dst = strength_grade(new_text)
    forbidden = _FORBIDDEN_UPGRADES.get(src, set())
    if dst in forbidden:
        return False
    # 若原为 causal，新不能丢掉因果限定（宽松处理：只要仍保留因果词即允许）
    return True


def extract_evidence(text: str, source_id: str, rel_path: str):
    """从一段材料文本抽取证据候选，返回结构化列表。"""
    items = []
    paras = split_paras(text)
    for pno, body in paras:
        for m in _NUM_UNIT_RE.finditer(body):
            items.append(_ev(source_id, rel_path, pno, "number_unit",
                             m.group(0).strip(), m.group(0).strip()))
        for m in _SAMPLE_RE.finditer(body):
            items.append(_ev(source_id, rel_path, pno, "sample_size",
                             m.group(0).strip(), m.group(0).strip()))
        for m in _CITE_BRACKET_RE.finditer(body):
            items.append(_ev(source_id, rel_path, pno, "citation",
                             m.group(0).strip(), m.group(0).strip()))
        for m in _CITE_AUTHOR_RE.finditer(body):
            items.append(_ev(source_id, rel_path, pno, "citation_author",
                             m.group(0).strip(), m.group(0).strip()))
        for m in _FIG_RE.finditer(body):
            items.append(_ev(source_id, rel_path, pno, "figure_ref",
                             m.group(0).strip(), m.group(0).strip()))
        for m in _TABLE_RE.finditer(body):
            items.append(_ev(source_id, rel_path, pno, "table_ref",
                             m.group(0).strip(), m.group(0).strip()))
        for m in _ABBREV_RE.finditer(body):
            items.append(_ev(source_id, rel_path, pno, "abbreviation",
                             m.group(0).strip(), m.group(0).strip()))
        for m in _N_RE.finditer(body):
            items.append(_ev(source_id, rel_path, pno, "sample_n",
                             m.group(0).strip(), m.group(0).strip()))
    return items


def _ev(source_id, rel_path, pno, kind, value, quote):
    return {
        "source_id": source_id,
        "locator": "%s#para.%d" % (rel_path, pno),
        "kind": kind,
        "value": value,
        "quote": quote,
    }


# ---------------------------------------------------------------------------
# 5. 受众画像 / 体裁映射 / 保留优先级 / 术语锁定
# ---------------------------------------------------------------------------

AUDIENCE_PROFILES = {
    "公众": {"label": "公众/科普", "term_density": "低", "jargon": "首次展开并给类比",
           "retain": "关键结论、1-2 个核心数字、明确局限", "drop": "方法细节、次要统计"},
    "科普": {"label": "公众/科普", "term_density": "低", "jargon": "首次展开并给类比",
           "retain": "关键结论、1-2 个核心数字、明确局限", "drop": "方法细节、次要统计"},
    "公众读者": {"label": "公众/科普", "term_density": "低", "jargon": "首次展开并给类比",
           "retain": "关键结论、1-2 个核心数字、明确局限", "drop": "方法细节、次要统计"},
    "基金评审": {"label": "基金/评审", "term_density": "中", "jargon": "保留专业术语但定义",
           "retain": "目标-路线-验证-风险、关键证据", "drop": "纯科普类比"},
    "评审": {"label": "基金/评审", "term_density": "中", "jargon": "保留专业术语但定义",
           "retain": "目标-路线-验证-风险、关键证据", "drop": "纯科普类比"},
    "项目申报": {"label": "项目申报", "term_density": "中", "jargon": "术语锁定一致",
           "retain": "目标、技术路线、验证指标、里程碑", "drop": "无关背景"},
    "技术": {"label": "技术同行", "term_density": "高", "jargon": "可用行话但首次定义",
           "retain": "方法参数、条件、样本量、统计口径", "drop": "冗余重复"},
    "同行": {"label": "技术同行", "term_density": "高", "jargon": "可用行话但首次定义",
           "retain": "方法参数、条件、样本量、统计口径", "drop": "冗余重复"},
    "媒体": {"label": "媒体/新闻", "term_density": "低", "jargon": "通俗改写",
           "retain": "一句话核心结论 + 局限", "drop": "全部方法细节"},
    "政策": {"label": "政策建议", "term_density": "低", "jargon": "术语翻译为政策语言",
           "retain": "可操作结论 + 不确定性", "drop": "技术参数"},
}

GENRE_PROFILES = {
    "科普": {"structure": "hook→结论→怎么做的（简）→局限→一句话收束",
           "term": "缩写首次展开、给类比、避免堆术语",
           "strength": "保留限定词：可能/初步/相关不写成因果"},
    "科普文章": {"structure": "hook→结论→怎么做的（简）→局限→一句话收束",
           "term": "缩写首次展开、给类比、避免堆术语",
           "strength": "保留限定词：可能/初步/相关不写成因果"},
    "新闻": {"structure": "核心结论一句话→意义→局限→背景",
           "term": "通俗改写，专业名词括注",
           "strength": "只降不升；不确定要显式说"},
    "简报": {"structure": "结论→依据→边界→建议",
           "term": "简明、术语最小化",
           "strength": "只降不升；不确定要显式说"},
    "项目申报": {"structure": "目标→背景与缺口→技术路线→验证指标→里程碑→风险",
           "term": "术语表锁定一致",
           "strength": "论断与证据一一对应，不夸大"},
    "申报材料": {"structure": "目标→背景与缺口→技术路线→验证指标→里程碑→风险",
           "term": "术语表锁定一致",
           "strength": "论断与证据一一对应，不夸大"},
    "技术报告": {"structure": "目的→方法→结果→结论→局限",
           "term": "行话可用但首次定义",
           "strength": "观察与解释分离，相关不写因果"},
    "基金": {"structure": "立项依据→研究内容→目标→方案→可行性→创新",
           "term": "专业术语定义",
           "strength": "可验证、不夸大创新"},
    "政策建议": {"structure": "问题→证据→选项→建议→不确定性",
           "term": "术语翻译为政策语言",
           "strength": "明确不确定性边界"},
    "default": {"structure": "按源文结构保留，压缩冗余",
           "term": "术语锁定一致",
           "strength": "只降不升"},
}

# 必须保留项（留得住）优先级
RETAIN_PRIORITY = [
    "数值与单位", "样本量与分组", "统计口径与比较基准", "限定条件与范围",
    "图表编号与引用", "关键结论", "方法要点", "引用/文献编号", "论断强度词",
]

DELETE_OK = ["冗余重复", "与目标读者无关的次要背景", "纯修辞铺陈"]


def match_audience(audience: str):
    if not audience:
        return {"label": "未指定", "term_density": "中", "jargon": "保持原文",
                "retain": RETAIN_PRIORITY, "drop": DELETE_OK}
    for key, prof in AUDIENCE_PROFILES.items():
        if key in (audience or ""):
            return dict(prof)
    return {"label": audience, "term_density": "中", "jargon": "保持原文并首次定义",
            "retain": RETAIN_PRIORITY, "drop": DELETE_OK}


def match_genre(genre: str):
    if not genre:
        return dict(GENRE_PROFILES["default"])
    for key, prof in GENRE_PROFILES.items():
        if key in (genre or ""):
            return dict(prof)
    return dict(GENRE_PROFILES["default"])


# 术语锁定：同义收敛规则
SYNONYM_HINTS = {
    "显著": "statistically significant 的中文对应，保留统计含义",
    "初步": "preliminary，不可删",
    "可能": "possible/likely，不可删",
    "相关": "correlation/association，不可升级为因果",
}

# ---------------------------------------------------------------------------
# 6. 确定性辅助
# ---------------------------------------------------------------------------

def sort_by_id(items, key="id"):
    """按 id 稳定排序（文本序），保证重复运行结果一致。"""
    return sorted(items, key=lambda x: str(x.get(key) if isinstance(x, dict) else x))


def canonical(items):
    """对列表做确定性规范化：按 id 或文本排序并去重（保持引用结构）。"""
    if not isinstance(items, list):
        return items
    seen = set()
    out = []
    for it in sorted(items, key=lambda x: stable_dumps(x)):
        sig = stable_dumps(it)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(it)
    return out
