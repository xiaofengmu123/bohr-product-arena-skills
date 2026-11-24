"""selftest.py - deterministic unit tests for core_impl + a mini end-to-end run.

Exit code 0 on success. Uses only the standard library. The end-to-end test
builds synthetic data in a temp directory and runs the full pipeline, so it
does not depend on /input existing on the developer machine.
"""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as S  # noqa: E402


def _close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


def test_distributions():
    assert _close(S.normal_ppf(0.975), 1.95996398454, 1e-6), "normal_ppf(0.975)"
    assert _close(S.normal_cdf(1.96), 0.97500210485, 1e-6), "normal_cdf(1.96)"
    assert _close(S.chi2_cdf(3.841458820694124, 1), 0.95, 1e-6), "chi2_cdf"
    assert _close(S.t_cdf(0.0, 5), 0.5, 1e-6), "t_cdf(0)"
    assert _close(S.t_pvalue(1.0, 1), 0.5, 1e-6), "t two-sided p, df=1"
    assert _close(S.t_pvalue(1.96, 1000), 0.0502, 1e-3), "t p large df"
    assert _close(S.quantile([1, 2, 3, 4, 5], 0.5), 3.0), "median"
    assert _close(S.quantile([1, 2, 3, 4], 0.25), 1.75), "q1 interpolation"
    return True


def test_descriptives():
    x = [1, 2, 3, 4, 5]
    assert _close(S.mean(x), 3.0)
    assert _close(S.std(x), 1.58113883008, 1e-6)
    assert _close(S.skewness(x), 0.0, 1e-6)
    assert _close(S.kurtosis(x), -1.2, 1e-6)  # bias-corrected excess kurtosis
    return True


def test_tests_consistency():
    # t and Mann-Whitney on identical shifted samples should agree direction.
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    b = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]
    t, df, p, method, eq, diff, se = S.t_test_ind(a, b)
    assert t is not None and p is not None and 0 < p <= 1
    U, pm, z, rbis = S.mannwhitney_u(a, b)
    assert U is not None and 0 <= pm <= 1
    F, pa, d1, d2, eta2, om2 = S.anova_oneway([a, b, [x + 0.5 for x in a]])
    assert F is not None and 0 <= pa <= 1 and 0 <= eta2 <= 1
    H, ph, dfh = S.kruskal_wallis([a, b, [x + 0.5 for x in a]])
    assert H is not None and 0 <= ph <= 1
    W, psw = S.shapiro_wilk([random.gauss(0, 1) for _ in range(30)])
    assert W is not None and 0 <= W <= 1 and 0 <= psw <= 1
    return True


def test_monte_carlo_uniformity():
    """Under H0, p-values should be roughly uniform (a real implementation check)."""
    random.seed(20261123)
    n_pvals = 300
    sw_p = []
    for _ in range(n_pvals):
        x = [random.gauss(0, 1) for _ in range(40)]
        W, p = S.shapiro_wilk(x)
        sw_p.append(p)
    mean_p = sum(sw_p) / len(sw_p)
    frac_low = sum(1 for p in sw_p if p < 0.05) / len(sw_p)
    assert 0.40 <= mean_p <= 0.60, f"SW mean p={mean_p}"
    assert 0.02 <= frac_low <= 0.10, f"SW frac<0.05={frac_low}"

    t_p = []
    for _ in range(300):
        a = [random.gauss(0, 1) for _ in range(20)]
        b = [random.gauss(0, 1) for _ in range(20)]
        t, df, p, *_ = S.t_test_ind(a, b)
        t_p.append(p)
    mean_t = sum(t_p) / len(t_p)
    assert 0.40 <= mean_t <= 0.60, f"t mean p={mean_t}"
    return True


def test_end_to_end():
    tmp = tempfile.mkdtemp(prefix="analysis02_selftest_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        case = {
            "case_id": "selftest-case",
            "task_instruction": "Compare value across three groups.",
            "output_contract": {
                "descriptive": [], "inferential": [], "effects": [],
                "interpretation": {}, "quality": [], "unknown_key": None,
            },
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# selftest task\n")
        rows = ["group,value"]
        for gi, gname in enumerate(("A", "B", "C")):
            for i in range(30):
                rows.append(f"{gname},{gi + i * 0.5 + random.gauss(0, 1):.4f}")
        # inject one duplicate and one missing cell (missing handled by design)
        rows.append("A,0.5")
        rows.append("C,")
        with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(rows) + "\n")

        import main as M
        rc = M.main(["all", "--input", inp, "--output", out, "--work", work])
        assert rc == 0, "pipeline exit code"
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        assert r.get("case_id") == "selftest-case"
        assert r.get("status") in ("ok", "partial")
        for k in ("descriptive", "inferential", "effects", "interpretation",
                  "quality", "unknown_key"):
            assert k in r, f"contract key {k} backfilled"
        assert "sources" in r and "uncertainty" in r and "change_log" in r
        assert os.path.exists(os.path.join(out, "analysis_report.md"))
        svg = os.path.join(out, "figures", "figure.svg")
        assert os.path.exists(svg)
        import xml.etree.ElementTree as ET
        ET.parse(svg)
        # determinism: run analyze again and compare analysis.json
        sc = M.main(["selfcheck", "--input", inp, "--output", out, "--work", work])
        assert sc == 0
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    tests = [test_distributions, test_descriptives, test_tests_consistency,
             test_monte_carlo_uniformity, test_end_to_end]
    for t in tests:
        name = t.__name__
        try:
            t()
            print(f"PASS {name}")
        except AssertionError as exc:
            print(f"FAIL {name}: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {name}: {exc}")
            return 1
    print("selftest: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
