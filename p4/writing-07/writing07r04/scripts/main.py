"""main.py — 引言与相关工作写作 Skill 的入口包装器（纯标准库）。

子命令：
  scan     扫描 /input，产出 work/input_manifest.json 与 work/case_normalized.json
  emit     基于 work/ 先落盘一版 partial result.json 骨架
  validate 校验 /app/output/result.json 与主产物，输出报告
  selftest 运行内置自检（等价于 scripts/selftest.py）

缺省路径：--input /input --output /app/output --work /app/work。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C  # noqa: E402


USAGE = """用法:
  python3 -B scripts/main.py scan      [--input DIR] [--work DIR]
  python3 -B scripts/main.py emit      [--work DIR] [--output DIR]
  python3 -B scripts/main.py validate  [--work DIR] [--output DIR]
  python3 -B scripts/main.py selftest

缺省: --input /input --output /app/output --work /app/work
"""


def parse_args(argv):
    """极简参数解析：支持 --key value，key 归一化为去 -- 的小写名。"""
    args = {
        "input": C.DEFAULT_INPUT,
        "output": C.DEFAULT_OUTPUT,
        "work": C.DEFAULT_WORK,
        "help": False,
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            args["help"] = True
        elif a.startswith("--"):
            key = a[2:].lower()
            if key in args:
                if i + 1 < len(argv):
                    args[key] = argv[i + 1]
                    i += 1
            # 未知参数静默忽略，避免因多余参数崩溃
        i += 1
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE)
        return 0
    cmd = argv[0].lower()
    if cmd in ("-h", "--help"):
        print(USAGE)
        return 0
    args = parse_args(argv[1:])
    if args["help"]:
        print(USAGE)
        return 0

    if cmd == "scan":
        manifest = C.scan_input(args["input"], args["work"])
        print("case_id=%s fields=%d materials=%d errors=%d empty=%s"
              % (manifest["case_id"], len(manifest["contract_fields"]),
                 len(manifest["materials"]), len(manifest["errors"]),
                 manifest["input_empty"]))
        return 0

    if cmd == "emit":
        result = C.emit_skeleton(args["work"], args["output"])
        print("status=%s case_id=%s contract_fields=%d"
              % (result["status"], result["case_id"],
                 len([k for k in result if k not in C.STANDARD_FIELDS and k not in ("case_id", "runtime")])))
        return 0

    if cmd == "validate":
        return C.run_validate(args["work"], args["output"])

    if cmd == "selftest":
        selftest_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest.py")
        if not os.path.isfile(selftest_path):
            print("selftest.py missing", file=sys.stderr)
            return 1
        import importlib.util
        spec = importlib.util.spec_from_file_location("_selftest", selftest_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.main()

    print("未知子命令: %s" % cmd, file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
