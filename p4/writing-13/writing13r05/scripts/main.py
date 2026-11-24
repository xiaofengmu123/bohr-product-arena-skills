#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grant-proposal-writing-verifier 运行时脚手架（仅 Python 标准库）。

把「基金申请书写作 + 可复核交付」变成一条增量、可回检的流程：

  init     读 case.json，先落盘 result.json partial 骨架（契约字段逐一对齐 output_contract）
  template 写七要素章节稿骨架到 /app/output/proposal_draft.md（可编辑主产物占位）
  sources  快照 /input 材料：大小 + SHA256 -> work/source_manifest.json
  ledger   事实台账 add/list/verify/export（locator + 逐字 quote + element 三级对应）
  log      向 result.json 追加 change_log 条目（增量改，不推倒重写）
  check    按 output_contract 逐字段自检 result.json，含一致性回检（--fix 补位）

路径约定：默认 /input、/app/output、/app/work；目录不存在时回退到当前
工作目录下的 input/、output/、work/（Windows 本地调试）。
所有 JSON 写入先落 .tmp 再原子替换，重复运行结果一致、不崩溃。
"""

import argparse
import datetime
import hashlib
import json
import os
import sys

# 跨平台确定性输出：强制 UTF-8，避免 Windows 控制台 GBK 导致中文乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))

# 默认输出契约：与题面「目标产物 / 事实与来源映射 / 版本与边界」一一对应。
# 若 case.json 显式给出 output_contract/output_schema/required_fields，以题面字段为最高优先级。
DEFAULT_CONTRACT = {
    "artifact": "目标产物：可直接进入申请书的可编辑章节稿与要素对照说明",
    "fact_source_map": "事实与来源映射：数字/术语/引用/图表/关键论断 -> locator + 逐字 quote",
    "version_boundary": "版本与边界：修改差异、人工确认项、材料缺口、异常",
}

# 除契约字段外必须恒有的字段（题面交付要求）。
ALWAYS_FIELDS = [
    "case_id", "status", "artifact", "fact_source_map", "version_boundary",
    "sources", "uncertainty", "pending_human", "change_log",
]

# 恒有字段中默认应为数组者。
LIST_FIELDS = {"sources", "uncertainty", "pending_human", "change_log", "fact_source_map"}

# 基金申请书七要素（评分维度「七个要素是否齐全」的默认集；
# 若运行时 organizer-workorder.json / case.json 给出不同要素清单，以其为准）。
SEVEN_ELEMENTS = [
    "立项依据", "科学问题", "研究目标", "研究内容",
    "技术路线", "创新点", "研究基础与可行性",
]

VALID_STATUS = ("in_progress", "done", "partial", "failed")


def now_iso():
    """本地时区 ISO 时间戳，用于 change_log / 快照。"""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_dir(p):
    try:
        os.makedirs(p, exist_ok=True)
    except OSError:
        pass
    return p


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def write_json(obj, path):
    """先写 .tmp 再原子替换，避免中途异常留下半截产物。"""
    d = os.path.dirname(path) or "."
    ensure_dir(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_json(path, tolerant=False):
    """容错读 JSON。utf-8-sig + errors=replace；tolerant 时坏 JSON 不抛异常。"""
    if not path or not os.path.exists(path):
        return None, "missing"
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            raw = f.read()
    except OSError as e:
        return None, "unreadable: %s" % e
    if not raw.strip():
        return None, "empty"
    try:
        return json.loads(raw), "ok"
    except json.JSONDecodeError as e:
        if not tolerant:
            return None, "bad-json: %s" % e
        # 尽力从尾部截断，恢复最后一个可解析对象
        for cut in range(len(raw), 0, -1):
            try:
                return json.loads(raw[:cut]), "truncated-fixed"
            except json.JSONDecodeError:
                continue
        return None, "bad-json: %s" % e


def extract_case_id(case):
    if not isinstance(case, dict):
        return None
    for k in ("case_id", "id", "caseId", "challenge_id", "challengeId"):
        v = case.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def extract_contract(case):
    """合并题面 output_contract / output_schema / required_fields；缺省用 DEFAULT_CONTRACT。

    优先级：output_contract > output_schema > required_fields > DEFAULT_CONTRACT。
    返回 (merged_dict, 最高优先级键名)。dict 值为字段描述；list 值为字段名。
    """
    if not isinstance(case, dict):
        return dict(DEFAULT_CONTRACT), None
    merged = dict(DEFAULT_CONTRACT)  # 默认契约始终兜底，保证三字段恒在
    found = []
    # 低优先级先放、高优先级后覆盖；list 项只 setdefault（不覆盖已有描述）
    for k in ("required_fields", "output_schema", "output_contract"):
        v = case.get(k)
        if isinstance(v, dict) and v:
            merged.update(v)
            found.append(k)
        elif isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            for name in v:
                merged.setdefault(name, None)
            found.append(k)
    return merged, (found[-1] if found else None)


def resolve_input():
    if os.path.isdir("/input"):
        return "/input"
    if os.path.isdir("input"):
        return "input"
    return "/input"


def resolve_out():
    if os.path.isdir("/app"):
        return "/app/output"
    return "output"


def resolve_work():
    if os.path.isdir("/app"):
        return "/app/work"
    return "work"


def resolve_case(case):
    if case:
        return case
    for c in ("/input/case.json", "input/case.json", "./input/case.json"):
        if os.path.exists(c):
            return c
    return "/input/case.json"


# ---------------------------------------------------------------- init

def cmd_init(args):
    case_path = resolve_case(args.case)
    out_dir = ensure_dir(args.out or resolve_out())
    work_dir = ensure_dir(args.work or resolve_work())

    case, cstate = load_json(case_path, tolerant=True)
    contract, ckey = extract_contract(case)
    case_id = extract_case_id(case) or "unknown"

    result = {"case_id": case_id, "status": args.status if args.status in VALID_STATUS else "partial"}
    # 契约字段逐一对齐：先占位为 null，由 Agent 增量填充；未知键原样保留、不删
    for k in contract:
        result[k] = None
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [{
        "ts": now_iso(),
        "action": "skeleton_init",
        "target": "result.json",
        "reason": "执行预算前 3 分钟先落盘 partial 骨架，之后只做增量改进",
        "impact": "所有契约字段已占位，待逐段填充",
        "status": result["status"],
    }]

    write_json(result, os.path.join(out_dir, "result.json"))
    meta = {
        "case_path": os.path.abspath(case_path),
        "case_load_state": cstate,
        "case_id": case_id,
        "contract_key": ckey,
        "contract_fields": list(contract.keys()),
        "contract_hint": contract,
        "always_fields": list(ALWAYS_FIELDS),
        "seven_elements": list(SEVEN_ELEMENTS),
        "created_ts": now_iso(),
    }
    write_json(meta, os.path.join(work_dir, "run_meta.json"))
    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "contract_key": ckey,
        "contract_fields": list(contract.keys()),
        "out": os.path.abspath(os.path.join(out_dir, "result.json")),
        "work_meta": os.path.abspath(os.path.join(work_dir, "run_meta.json")),
    }, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- template

TEMPLATE_MD = """# 基金申请书章节稿（七要素 + 要素对照说明）

> 说明：本文件为可编辑初稿骨架，由执行 Agent 依据 /input 材料逐段填充。
> 方括号为待补位；[已完成] 与 [拟开展] 必须严格区分；不得新增材料外事实。
> 每个数字/术语/引用/关键论断需在 §0 或 §9 记录 locator + 逐字 quote。

## 0. 要素对照说明（三级对应：段落—要素—证据）
| 段落/要素 | 关键论断 | 证据 locator | 逐字 quote | 已完成/拟开展 |
|---|---|---|---|---|

## 1. 立项依据
### 1.1 领域现状与关键瓶颈（别人做到哪一步）
### 1.2 必要性论证（还差什么、为什么是我们）
### 1.3 为什么现在必须做（避免政策口号堆叠）

## 2. 科学问题
### 2.1 问题收敛（可回答、可分解、有边界）

## 3. 研究目标
### 3.1 目标（数量适度、层次清楚、可考核）

## 4. 研究内容
### 4.1 内容（与研究目标一一对应）

## 5. 技术路线
### 5.1 步骤、方法与关键节点（输入/输出/可行性依据/风险与备选方案）

## 6. 创新点
### 6.1 与已有工作的具体差异（新在哪、为什么难、凭什么做得成）

## 7. 研究基础与可行性
### 7.1 能力—证据—与本研究的关系（证据链）

## 8. 目标—内容—路线—指标 对应表
| 研究目标 | 研究内容 | 技术路线步骤 | 考核指标 | 证据 |
|---|---|---|---|---|

## 9. 引用与来源清单
（locator + 逐字 quote；已完成 vs 拟开展）
"""


def cmd_template(args):
    out_dir = ensure_dir(args.out or resolve_out())
    path = os.path.join(out_dir, "proposal_draft.md")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(TEMPLATE_MD)
    print(json.dumps({"ok": True, "artifact": os.path.abspath(path)}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- sources

def cmd_sources(args):
    input_dir = args.input or resolve_input()
    work_dir = ensure_dir(args.work or resolve_work())
    if not os.path.isdir(input_dir):
        print(json.dumps({"ok": False, "error": "input dir missing", "input": input_dir},
                         ensure_ascii=False))
        return 1

    manifest = {"snapshot_ts": now_iso(), "input_dir": os.path.abspath(input_dir), "files": []}
    max_bytes = int((args.max_size or 2.0) * 1024 * 1024)
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        files.sort()
        for fn in files:
            p = os.path.join(root, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            if size > max_bytes:
                manifest["files"].append({"path": rel, "size": size, "sha256": None,
                                          "note": "exceeds %.1fMB, skipped" % (args.max_size or 2.0)})
                continue
            manifest["files"].append({"path": rel, "size": size, "sha256": sha256_file(p)})

    manifest_path = os.path.join(work_dir, "source_manifest.json")
    write_json(manifest, manifest_path)
    print(json.dumps({"ok": True, "count": len(manifest["files"]),
                      "manifest": os.path.abspath(manifest_path)}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- ledger

def load_ledger(work_dir):
    path = os.path.join(work_dir, "ledger.json")
    data, _ = load_json(path, tolerant=False)
    if isinstance(data, list):
        return data
    return []


def cmd_ledger(args):
    work_dir = ensure_dir(args.work or resolve_work())
    ledger = load_ledger(work_dir)
    op = args.op

    if op == "add":
        entry = {
            "claim": args.claim or "",
            "element": args.element or "",
            "kind": args.kind or "fact",
            "source_ref": args.ref or "",
            "locator": args.loc or "",
            "quote": args.quote or "",
            "status": args.status or "mapped",
            "note": args.note or "",
            "ts": now_iso(),
        }
        ledger.append(entry)
        write_json(ledger, os.path.join(work_dir, "ledger.json"))
        print(json.dumps({"ok": True, "count": len(ledger), "added": entry["claim"]},
                         ensure_ascii=False))
        return 0

    if op == "list":
        print(json.dumps(ledger, ensure_ascii=False, indent=2))
        return 0

    if op == "verify":
        problems = []
        for i, e in enumerate(ledger):
            if not isinstance(e, dict):
                continue
            if e.get("status") == "mapped":
                if not e.get("locator") or not e.get("quote"):
                    problems.append({"index": i, "claim": e.get("claim"), "issue": "mapped 但缺 locator/quote"})
            if e.get("status") not in ("mapped", "unmapped", "conflict", None, ""):
                problems.append({"index": i, "claim": e.get("claim"), "issue": "status 取值非法"})
        summary = {
            "total": len(ledger),
            "mapped": sum(1 for e in ledger if isinstance(e, dict) and e.get("status") == "mapped"),
            "unmapped": sum(1 for e in ledger if isinstance(e, dict) and e.get("status") == "unmapped"),
            "conflict": sum(1 for e in ledger if isinstance(e, dict) and e.get("status") == "conflict"),
            "problems": problems,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if op == "export":
        out_dir = ensure_dir(args.out or resolve_out())
        path = os.path.join(out_dir, "fact_source_map.json")
        write_json(ledger, path)
        print(json.dumps({"ok": True, "exported": os.path.abspath(path)}, ensure_ascii=False))
        return 0

    print(json.dumps({"ok": False, "error": "unknown op: %s" % op}, ensure_ascii=False))
    return 1


# ---------------------------------------------------------------- log

def cmd_log(args):
    out_dir = ensure_dir(args.out or resolve_out())
    rp = os.path.join(out_dir, "result.json")
    result, state = load_json(rp, tolerant=False)
    if not isinstance(result, dict):
        result = {"case_id": None, "status": "partial"}
    if not isinstance(result.get("change_log"), list):
        result["change_log"] = []
    entry = {
        "ts": now_iso(),
        "action": args.action,
        "target": args.target or "result.json",
        "reason": args.reason or "",
        "impact": args.impact or "",
        "status": args.status or result.get("status", "partial"),
    }
    result["change_log"].append(entry)
    write_json(result, rp)
    print(json.dumps({"ok": True, "entries": len(result["change_log"])}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- check

def cmd_check(args):
    case_path = resolve_case(args.case)
    out_dir = args.out or resolve_out()
    rp = os.path.join(out_dir, "result.json")
    result, state = load_json(rp, tolerant=False)

    report = {"check_ts": now_iso(), "items": []}
    hard_fail = False
    warnings = 0

    def item(ok, level, msg):
        nonlocal hard_fail, warnings
        report["items"].append({"ok": bool(ok), "level": level, "msg": msg})
        if not ok and level == "hard":
            hard_fail = True
        if not ok and level == "warn":
            warnings += 1

    if state != "ok" or not isinstance(result, dict):
        item(False, "hard", "result.json 缺失或非法 JSON（state=%s）" % state)
        report["ok"] = False
        report["warnings"] = warnings
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    case, _ = load_json(case_path, tolerant=True)
    contract, ckey = extract_contract(case)
    contract_fields = list(contract.keys())

    item(bool(result.get("case_id")), "hard", "case_id 非空")
    item(result.get("status") in VALID_STATUS, "hard",
         "status 取值合法（in_progress/done/partial/failed）")

    for k in contract_fields:
        if k in result:
            v = result[k]
            if v is None or v == [] or v == {}:
                item(False, "warn", "契约字段 '%s' 已占位但为空" % k)
            else:
                item(True, "ok", "契约字段 '%s' 已填充" % k)
        else:
            item(False, "hard", "契约字段 '%s' 缺失（必须回填，缺值给 null）" % k)

    for k in ALWAYS_FIELDS:
        item(k in result, "hard" if k not in result else "ok",
             "恒有字段 '%s' 存在" % k if k in result else "恒有字段 '%s' 缺失" % k)

    fsm = result.get("fact_source_map")
    if isinstance(fsm, list) and fsm:
        no_loc = sum(1 for e in fsm if isinstance(e, dict) and e.get("status") == "mapped"
                     and (not e.get("locator") or not e.get("quote")))
        no_ele = sum(1 for e in fsm if isinstance(e, dict) and e.get("status") == "mapped"
                     and not e.get("element"))
        if no_loc:
            item(False, "warn", "fact_source_map 有 %d 条 mapped 缺 locator/quote" % no_loc)
        else:
            item(True, "ok", "fact_source_map 每条 mapped 均有 locator + 逐字 quote")
        if no_ele:
            item(False, "warn", "fact_source_map 有 %d 条 mapped 缺 element（三级对应不完整）" % no_ele)
    else:
        item(False, "warn", "fact_source_map 为空或非数组")

    srcs = result.get("sources")
    if isinstance(srcs, list) and srcs:
        no_sha = sum(1 for s in srcs if isinstance(s, dict) and not s.get("sha256"))
        if no_sha:
            item(False, "warn", "sources 有 %d 条缺 sha256" % no_sha)
        else:
            item(True, "ok", "sources 全部含 sha256（可复核）")
    else:
        item(False, "warn", "sources 为空")

    cl = result.get("change_log")
    if isinstance(cl, list) and cl:
        bad = sum(1 for e in cl if not isinstance(e, dict) or not e.get("action") or not e.get("ts"))
        if bad:
            item(False, "warn", "change_log 有 %d 条缺 ts/action" % bad)
        else:
            item(True, "ok", "change_log 共 %d 条且字段完整" % len(cl))
    else:
        item(False, "warn", "change_log 为空")

    elements = result.get("elements")
    if isinstance(elements, dict):
        missing = [n for n in SEVEN_ELEMENTS if not elements.get(n)]
        if missing:
            item(False, "warn", "七要素缺 %d 项：%s" % (len(missing), "、".join(missing)))
        else:
            item(True, "ok", "七要素齐全")
        # 「对得上」：目标—内容—路线—指标 对应表
        corr = result.get("correspondence") or result.get("goal_content_route_indicator")
        if isinstance(corr, list) and corr:
            item(True, "ok", "目标—内容—路线—指标对应表存在（%d 行）" % len(corr))
        else:
            item(False, "warn", "缺目标—内容—路线—指标对应表（「对得上」无法核验）")

    if args.fix:
        changed = False
        for k in contract_fields:
            if k not in result:
                result[k] = None
                changed = True
        for k in ALWAYS_FIELDS:
            if k not in result:
                result[k] = [] if k in LIST_FIELDS else None
                changed = True
        if changed:
            write_json(result, rp)
            report["fixed"] = True

    report["ok"] = not hard_fail
    report["warnings"] = warnings
    report["contract_fields"] = contract_fields
    report["contract_key"] = ckey
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if hard_fail else 0


# ---------------------------------------------------------------- dispatch

def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="grant-proposal-writing-verifier 脚手架")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="落盘 result.json partial 骨架")
    pi.add_argument("--case", default=None)
    pi.add_argument("--out", default=None)
    pi.add_argument("--work", default=None)
    pi.add_argument("--status", default="partial", choices=list(VALID_STATUS))

    pt = sub.add_parser("template", help="写七要素章节稿骨架")
    pt.add_argument("--out", default=None)

    ps = sub.add_parser("sources", help="快照输入材料（大小+SHA256）")
    ps.add_argument("--input", default=None)
    ps.add_argument("--work", default=None)
    ps.add_argument("--max-size", type=float, default=2.0)

    pl = sub.add_parser("ledger", help="事实台账 add/list/verify/export")
    pl.add_argument("op", choices=["add", "list", "verify", "export"])
    pl.add_argument("--work", default=None)
    pl.add_argument("--out", default=None)
    pl.add_argument("--element", default=None)
    pl.add_argument("--claim", default=None)
    pl.add_argument("--kind", default=None)
    pl.add_argument("--ref", default=None)
    pl.add_argument("--loc", default=None)
    pl.add_argument("--quote", default=None)
    pl.add_argument("--status", default=None)
    pl.add_argument("--note", default=None)

    pg = sub.add_parser("log", help="追加 change_log 条目")
    pg.add_argument("--out", default=None)
    pg.add_argument("--action", required=True)
    pg.add_argument("--target", default="result.json")
    pg.add_argument("--reason", default="")
    pg.add_argument("--impact", default="")
    pg.add_argument("--status", default=None)

    pc = sub.add_parser("check", help="按 output_contract 逐字段自检")
    pc.add_argument("--case", default=None)
    pc.add_argument("--out", default=None)
    pc.add_argument("--work", default=None)
    pc.add_argument("--fix", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "init": cmd_init,
        "template": cmd_template,
        "sources": cmd_sources,
        "ledger": cmd_ledger,
        "log": cmd_log,
        "check": cmd_check,
    }
    try:
        return handlers[args.cmd](args)
    except Exception as e:  # 任何异常都落盘最小信息，不让进程崩溃
        sys.stderr.write("runtime error: %s\n" % e)
        try:
            write_json({"ok": False, "error": str(e), "ts": now_iso()},
                       os.path.join(resolve_work(), "error.json"))
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
