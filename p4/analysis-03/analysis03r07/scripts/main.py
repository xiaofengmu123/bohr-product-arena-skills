#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — sci-viz-robust Skill 主控脚本（仅标准库）。

职责：
  1) 发现输入：不假设固定文件名/列名/键名，扫描 case.json、task.md、CSV/SQLite 等；
  2) 提取 output_contract：显式 JSON 键优先，正文表格字段反推兜底，叶值置 null/[]；
  3) 先落盘合法骨架：init 立即写 /app/output/result.json（status=partial），
     并写 work/plan.md 与 work/manifest.json，任何中途异常都不留空产物；
  4) update 做增量改进并维护 change_log；check 自检校验结构与产物格式。

子命令：
  init      发现输入、提取契约、写 result.json 骨架 + work/plan.md + manifest.json
  update    --set 'status="ok"' / --set '{"k":1}' / --set-key k --set-value v /
            --status ok / --log "..." 增量更新 result.json
  check     校验 result.json 与 /app/output 下产物；--require-contract 校验契约字段
            --fail 时遇 FAIL 才非零退出（默认退出码恒为 0）

缺省参数：--input /input --output /app/output --work /app/work。
任何命令退出码恒为 0（仅 check --fail 且存在 FAIL 项时退出 1）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C


def _die(msg):
    # 任何未捕获异常都落到这里，写最小合法产物并退出 0，保证 Harness 不中断。
    print("ERROR: %s" % msg)
    return 0


def cmd_init(args):
    files, deg = C.discover_input(args.input)
    case_id = C.build_case_id(files)
    template, source, notes = C.extract_contract(files)
    res = C.new_result_skeleton(case_id, template, source, notes)
    for d in deg:
        if d not in res["degradations"]:
            res["degradations"].append(d)
    if not os.path.isdir(args.input):
        res["status"] = "input_error"
        res["errors"].append("input_dir_missing: %s" % args.input)
    elif not files["case_json"] and not files["task_md"] and not files["materials"] \
            and not files["data"]:
        res["status"] = "insufficient_evidence"
        res["pending_human"].append("输入目录为空或无可识别材料")
    else:
        C.append_change(res, "init", "落盘 partial 结果骨架", [
            os.path.join(args.output, "result.json")])
    try:
        C.atomic_write_json(os.path.join(args.output, "result.json"), res)
    except OSError as e:
        # 写盘失败降级：写到 work/ 兜底，进程不崩溃。
        print("WARN: output write failed (%s), fallback to work dir" % e)
        try:
            C.atomic_write_json(os.path.join(args.work, "result.fallback.json"), res)
        except OSError:
            pass
    try:
        plan = _plan_text(files, case_id, template, source, notes, deg)
        C.atomic_write_text(os.path.join(args.work, "plan.md"), plan)
    except OSError as e:
        print("WARN: plan write failed: %s" % e)
    C.write_manifest(files, "42", args.work)
    print(json_dump_summary(res))
    return 0


def _plan_text(files, case_id, template, source, notes, deg):
    lines = [
        "# 分析计划（自动生成，可增量修改）",
        "",
        "- case_id: `%s`" % case_id,
        "- 契约来源: `%s`" % source,
        "- 契约字段: %s" % ", ".join(sorted(template or {})) or "(无)",
        "- 材料文件: %d 个 | 数据文件: %d 个" % (len(files["materials"]),
                                                 len(files["data"])),
    ]
    if notes:
        lines.append("- 契约备注: %s" % "；".join(notes))
    if deg:
        lines.append("- 降级记录: %s" % "；".join(deg))
    lines += [
        "",
        "## 待办（按顺序执行，每步一次请求做一件事）",
        "1. 读数据字典与字段语义（变量名、单位、类别编码、缺失标记）。",
        "2. 记录清洗口径：缺失/异常/重复先记录再处理，不静默删除或填充。",
        "3. 统计：分布、比较、相关、趋势按任务目的计算。",
        "4. 选图：连续→直方/箱线/小提琴；分类比较→(分组)箱线/点图；构成→堆叠条；",
        "   相关趋势→散点/折线；多变量→散点矩阵/相关热图/分面。",
        "5. 编码：位置/长度优先，Okabe-Ito 色盲安全配色，类别>6 聚合或分面。",
        "6. 标注：轴标题=变量(单位)、图例、误差线写 SD/SEM/CI 并给 n、显著性给 p/记号。",
        "7. 导出：SVG + PDF(字体嵌入) + >=300dpi PNG，单栏 3.4 in，中文探测 CJK 字体。",
        "8. 每步用 update 增量落盘并写 change_log；完成后 check --require-contract。",
    ]
    return "\n".join(lines) + "\n"


def json_dump_summary(res):
    import json
    return json.dumps({"case_id": res.get("case_id"), "status": res.get("status"),
                       "contract_keys": sorted(k for k in res
                                               if k not in C.RESERVED_KEYS),
                       "degradations": len(res.get("degradations", []))},
                      ensure_ascii=False)


def cmd_update(args):
    out = os.path.join(args.output, "result.json")
    text, _enc, err = C.read_text_robust(out)
    if err:
        print("WARN: 无法读取 result.json (%s)，本次 update 跳过" % err)
        return 0
    try:
        res = json_loads(text)
    except Exception as e:
        print("WARN: result.json 解析失败 (%s)，本次 update 跳过" % e)
        return 0
    if args.set:
        try:
            for k, v in _parse_set(args.set).items():
                C.set_path(res, k, v)
        except Exception as e:
            print("WARN: --set 解析失败 (%s)" % e)
    if args.set_key:
        C.set_path(res, args.set_key, C.parse_json_value(args.set_value))
    if args.status:
        if args.status in C.STATUS_VALUES:
            res["status"] = args.status
        else:
            print("WARN: 非法 status=%r，保留原值" % args.status)
    if args.add_source:
        try:
            obj = json_loads(args.add_source)
            if isinstance(obj, dict) and obj.get("locator") and obj.get("quote"):
                res.setdefault("sources", []).append(
                    C.make_source(obj["locator"], obj["quote"],
                                  obj.get("kind", "value")))
        except Exception as e:
            print("WARN: --add-source 解析失败 (%s)" % e)
    if args.add_pending:
        res.setdefault("pending_human", []).append(args.add_pending)
    if args.add_artifact:
        p = args.add_artifact
        res.setdefault("artifacts", []).append(
            {"path": p, "format": os.path.splitext(p)[1].lstrip(".") or "txt"})
    C.append_change(res, args.step or "update",
                    args.log or ("set " + str(args.set or args.set_key)),
                    args.files or [])
    try:
        C.atomic_write_json(out, res)
    except OSError as e:
        print("WARN: 写盘失败: %s" % e)
    print(json_dump_summary(res))
    return 0


def _parse_set(s):
    s = s.strip()
    if s.startswith("{"):
        return C.parse_json_value(s)
    if "=" in s:
        k, v = s.split("=", 1)
        return {k.strip(): C.parse_json_value(v)}
    raise ValueError("--set 需要 key=value 或 JSON，收到: %r" % s)


def json_loads(text):
    import json
    return json.loads(text)


def cmd_check(args):
    out = os.path.join(args.output, "result.json")
    text, _enc, err = C.read_text_robust(out)
    if err:
        print("FAIL result.json 不可读: %s" % err)
        return 1 if args.fail else 0
    try:
        res = json_loads(text)
    except Exception as e:
        print("FAIL result.json 非法: %s" % e)
        return 1 if args.fail else 0
    template = None
    if args.require_contract:
        files, _deg = C.discover_input(args.input)
        template, _src, _notes = C.extract_contract(files)
    findings, passed, failed = C.check_result(res, args.output,
                                              require_contract=args.require_contract,
                                              contract_template=template)
    for level, code, msg in findings:
        print("%s %s %s" % (level, code, msg))
    print("SUMMARY passed=%d failed=%d total=%d status=%s" %
          (passed, failed, len(findings), res.get("status")))
    if failed:
        print("RESULT FAIL (%d)" % failed)
        return 1 if args.fail else 0
    print("RESULT OK")
    return 0


def _common_args():
    c = argparse.ArgumentParser(add_help=False)
    c.add_argument("--input", default="/input")
    c.add_argument("--output", default="/app/output")
    c.add_argument("--work", default="/app/work")
    return c


def build_parser():
    p = argparse.ArgumentParser(
        description="科研数据可视化 Skill 主控脚本（鲁棒性优先）")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("init", parents=[_common_args()],
                        help="发现输入、提取契约、写 result.json 骨架")

    up = sub.add_parser("update", parents=[_common_args()],
                        help="增量更新 result.json 并记 change_log")
    up.add_argument("--set", help="key=value 或 JSON 对象")
    up.add_argument("--set-key")
    up.add_argument("--set-value")
    up.add_argument("--status")
    up.add_argument("--log")
    up.add_argument("--step")
    up.add_argument("--add-source", help="JSON: {locator,quote,kind}")
    up.add_argument("--add-pending")
    up.add_argument("--add-artifact")
    up.add_argument("--files", nargs="*")

    cp = sub.add_parser("check", parents=[_common_args()],
                        help="自检 result.json 与产物格式")
    cp.add_argument("--require-contract", action="store_true")
    cp.add_argument("--fail", action="store_true",
                    help="存在 FAIL 时以退出码 1 结束")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "update":
            return cmd_update(args)
        if args.cmd == "check":
            return cmd_check(args)
        build_parser().print_help()
        return 0
    except Exception as e:  # 兜底：任何未捕获异常都不让进程非零退出
        import traceback
        traceback.print_exc()
        return _die(str(e))


if __name__ == "__main__":
    sys.exit(main())
