"""main.py - 薄入口，转发到 core_impl（实现位于 core_impl.py）。

用法：
  python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
  python3 scripts/main.py finalize --output /app/output --work /app/work
  python3 scripts/main.py selftest --root .
"""
import os
import runpy
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py {bootstrap|finalize|validate|dedupe|rank|path|selftest} [options]")
    print("  bootstrap  --input DIR --output DIR --work DIR   扫描输入并写骨架+中间表示")
    print("  finalize   --output DIR --work DIR               由中间表示投影生成最终产物")
    print("  selftest   --root DIR                            对 Skill 包自检")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv or len(sys.argv) == 1:
        _help()
        raise SystemExit(0)
    target = os.path.join(_HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")