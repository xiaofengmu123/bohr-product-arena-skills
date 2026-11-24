"""main — 科研数据分析 Skill 的命令行入口（仅标准库）。

子命令：
  python3 -B scripts/main.py init      --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py profile   --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py validate  --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py selftest

--input/--output/--work 均可缺省：缺省分别指向 /input、/app/output、/app/work。
除 selftest 在场景失败时以非 0 退出外，其余命令在输入异常时仍产出合法结果并退出 0。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C  # noqa: E402

USAGE = """usage: main.py <command> [--input DIR] [--output DIR] [--work DIR]

commands:
  init     扫描输入并落盘 result.json 骨架（case_id + 契约字段 + status=partial）
  profile  生成 data_profile.json（编码/行列/字段/缺失/重复/数值摘要/SQLite 表结构）
  validate 校验 result.json 与产物（契约字段、sources 溯源、产物格式/存在性）
  selftest 运行空输入/坏 JSON/坏编码/缺参数四种自检场景

options:
  --input DIR   输入目录，缺省 /input
  --output DIR  产物目录，缺省 /app/output
  --work DIR    工作目录，缺省 /app/work
  -h, --help    显示本帮助
"""


def resolve_args(argv):
    args = {"input": C.DEFAULT_INPUT, "output": C.DEFAULT_OUTPUT, "work": C.DEFAULT_WORK}
    i = 0
    n = len(argv)
    while i < n:
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < n else None
        if a in ("--input", "-i") and nxt is not None:
            args["input"] = nxt
            i += 2
        elif a in ("--output", "-o") and nxt is not None:
            args["output"] = nxt
            i += 2
        elif a in ("--work", "-w") and nxt is not None:
            args["work"] = nxt
            i += 2
        elif a in ("-h", "--help"):
            print(USAGE)
            sys.exit(0)
        else:
            i += 1
    return args


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "-h", "--help"):
        print(USAGE)
        sys.exit(0)
    cmd = sys.argv[1]
    args = resolve_args(sys.argv[2:])
    if cmd == "init":
        C.run_init(args["input"], args["output"], args["work"])
        sys.exit(0)
    if cmd == "profile":
        C.run_profile(args["input"], args["output"], args["work"])
        sys.exit(0)
    if cmd == "validate":
        C.run_validate(args["input"], args["output"], args["work"])
        sys.exit(0)
    if cmd == "selftest":
        ok = C.run_selftest()
        sys.exit(0 if ok else 1)
    print(f"unknown command: {cmd}")
    print(USAGE)
    sys.exit(2)


if __name__ == "__main__":
    main()
