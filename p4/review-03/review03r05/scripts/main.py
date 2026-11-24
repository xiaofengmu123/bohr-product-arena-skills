"""main.py -- thin entry wrapper. Implementation lives in core_impl.py."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


if __name__ == "__main__":
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
