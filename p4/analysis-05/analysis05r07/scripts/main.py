"""main.py — thin CLI entry for the sci-data-analysis skill (robust variant).

This wrapper only locates and delegates to ``core_impl.py`` in the same
directory.  Keeping the dispatcher small means the implementation stays
independently runnable, importable by selftest, and easy to audit.

Exit codes:
    0   completed (even when the case was degraded / insufficient)
    2   invalid usage (only reachable if core_impl is missing)
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IMPL = os.path.join(HERE, "core_impl.py")

_USAGE = (
    "usage: python3 -B scripts/main.py [COMMAND] [options]\n"
    "\n"
    "COMMAND (default: run)\n"
    "  run         full pipeline: scaffold -> profile -> clean -> analyze -> render -> finalize\n"
    "  scaffold    write a partial result.json skeleton first (do this within 3 minutes)\n"
    "  profile     scan /input, build a field dictionary into work/profile.json\n"
    "  clean       deduplicate + missing/outlier accounting into work/cleaned.csv + clean_meta.json\n"
    "  analyze     descriptive stats, group tests, correlations into work/stats.json\n"
    "  render      emit report.md and editable SVG figures from work/stats.json\n"
    "  finalize    set status and fill all output_contract fields in result.json\n"
    "  query       run one SQL query against a discovered SQLite database\n"
    "\n"
    "options\n"
    "  --input DIR    input directory   (default /input)\n"
    "  --output DIR   output directory  (default /app/output)\n"
    "  --work DIR     working directory (default /app/work)\n"
    "  --primary NAME source file/table to analyze\n"
    "  --group COL    categorical column for group comparison\n"
    "  --targets A,B  comma-separated numeric columns to analyze\n"
    "  --sql SQL      SQL text for the 'query' command\n"
    "  --db NAME      SQLite file for the 'query' command\n"
    "  --help, -h     print this message\n"
)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in args):
        print(_USAGE)
        return 0
    if not os.path.exists(IMPL):
        sys.stderr.write("core_impl.py missing next to main.py\n")
        return 2
    sys.argv = [IMPL] + args
    runpy.run_path(IMPL, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
