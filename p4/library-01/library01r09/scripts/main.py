"""main.py wrapper"""
import os, runpy, sys
HERE=os.path.dirname(os.path.abspath(__file__))
if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print("usage: main.py"); raise SystemExit(0)
    sys.argv[0]=os.path.join(HERE,"core_impl.py")
    runpy.run_path(sys.argv[0], run_name="__main__")
