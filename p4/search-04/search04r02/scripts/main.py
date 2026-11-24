"""main.py — thin entry wrapper (implementation in core_impl.py)."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

USAGE = """usage: main.py <subcommand> [options]

subcommands:
  bootstrap    scan /input, echo output_contract, write partial result.json skeleton
  checkpoint   record a phase/checkpoint in state.json and append change_log
  dedupe       merge paper identities (DOI -> arXiv -> title+author+year -> title)
  rank         rank papers by distance/flow/recency/confidence
  path         compute a shortest citation path between two paper ids
  finalize     validate result.json and render report.md, papers.csv, edges.csv, SVG
  help         show this message
"""

if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
