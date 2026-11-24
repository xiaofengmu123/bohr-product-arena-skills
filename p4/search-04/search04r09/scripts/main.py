"""main.py -- 薄入口：实现全部在 core_impl.py，保持本文件 < 30KB。"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py {bootstrap|finalize|dedupe|rank|path|selftest} [options]")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h", "help"):
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")