"""selftest.py - 对 Skill 包自身运行自检（薄封装，实现见 core_impl.selftest）。

用法：
  python3 scripts/selftest.py [--root <skill 根目录>]
退出码 0 表示全部自检项通过；非 0 表示存在失败项。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

if __name__ == "__main__":
    root = _ROOT
    args = sys.argv[1:]
    if "--root" in args:
        i = args.index("--root")
        if i + 1 < len(args):
            root = args[i + 1]
    sys.path.insert(0, _HERE)
    import core_impl
    raise SystemExit(core_impl.selftest(root))