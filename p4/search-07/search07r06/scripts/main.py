"""main.py -- 薄入口，真正的实现在 core_impl.py。

用法（相对路径调用，脚本位于 scripts/ 下）：

    python scripts/main.py --help
    python scripts/main.py scaffold --input /input --output /app/output --work /app/work
    python scripts/main.py inspect  --input /input --output /app/output --work /app/work
    python scripts/main.py plan     --work /app/work [--plan-file p.json --freeze]
    python scripts/main.py build    --input /input --work /app/work [--refs a.json --results b.json]
    python scripts/main.py match    --work /app/work --output /app/output
    python scripts/main.py merge    --input /input --output /app/output --work /app/work [--updates u.json]
    python scripts/main.py report   --output /app/output --work /app/work
    python scripts/main.py full     --input /input --output /app/output --work /app/work

仅依赖 Python 标准库；--input/--output/--work 缺省分别指向 /input、/app/output、/app/work。
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py [--input DIR] [--output DIR] [--work DIR] [--help]")
    print("subcommands: scaffold | inspect | plan | build | match | merge | report | full")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
