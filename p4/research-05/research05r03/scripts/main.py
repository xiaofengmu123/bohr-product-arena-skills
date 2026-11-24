# -*- coding: utf-8 -*-
"""main.py — 科研选题评估 Skill 的命令行入口。

子命令：
  skeleton  --input /input --output /app/output --work /app/work   先落 partial 骨架
  evidence  --input /input --output /app/output --work /app/work   材料发现 + PDF/Office 抽字
  finalize  --input /input --output /app/output --work /app/work   合并 fragment + 校验 + report.md
  selftest  --skill <包根目录> --input <目录> --work <目录> --output <目录>

仅依赖 Python 标准库；路径缺省为 /input、/app/output、/app/work。
"""

import os
import sys
import importlib.util


def load_core():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("core_impl", os.path.join(here, "core_impl.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_selftest():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("selftest", os.path.join(here, "selftest.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_args(argv):
    args = {"input": "/input", "output": "/app/output", "work": "/app/work",
            "skill": None, "extra": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "--output", "--work", "--skill") and i + 1 < len(argv):
            args[a[2:]] = argv[i + 1]
            i += 2
        else:
            args["extra"].append(a)
            i += 1
    return args


USAGE = """用法：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py evidence --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py selftest --skill . --input <case> --output <case>/out --work <case>/work

子命令说明：
  skeleton  写 /app/output/result.json（status=partial，契约字段全量就位）+ /app/work/input_manifest.json
  evidence  写 /app/work/material_inventory.json，刷新 result.json 的 sources/degradations
  finalize  深合并 /app/work/fragments/*.json，校验契约，写 result.json 与 report.md（校验失败退出码 2）
  selftest  校验 SKILL.md frontmatter + result.json 契约 + 确定性（重合并应字节一致）
"""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd = argv[0]
    args = parse_args(argv[1:])
    core = load_core()

    if cmd == "skeleton":
        entries = core.discover_files(args["input"])
        case_data, case_error = core.safe_json_load(os.path.join(args["input"], "case.json"))
        contract, contract_error = core.resolve_contract(case_data)
        core.build_skeleton(args["input"], args["work"], args["output"],
                            case_data, case_error, entries, contract, contract_error)
        print("skeleton written:", os.path.join(args["output"], "result.json"))
        return 0

    if cmd == "evidence":
        core.run_evidence(args["input"], args["work"], args["output"])
        print("evidence written:", os.path.join(args["work"], "material_inventory.json"))
        return 0

    if cmd == "finalize":
        return core.run_finalize(args["input"], args["work"], args["output"])

    if cmd == "selftest":
        st = load_selftest()
        return st.run_selftest(skill_dir=args["skill"] or ".",
                               input_dir=args["input"], work_dir=args["work"],
                               output_dir=args["output"])

    print("未知子命令: %s" % cmd, file=sys.stderr)
    print(USAGE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
