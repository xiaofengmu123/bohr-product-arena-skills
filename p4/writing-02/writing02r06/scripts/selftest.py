# -*- coding: utf-8 -*-
"""Deterministic self-test for the Skill package.

Runs the pipeline (scaffold -> fill intermediate artifacts -> verify ->
finalize -> selfcheck) on synthetic cases that exercise the required failure
modes: empty input, bad JSON, bad encoding, minimal valid case, and a
deliberately violated invariant. Standard library only; no network.

Usage:
    python3 -B scripts/selftest.py
Exit code 0 iff every check passes.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main  # noqa: E402
import invariants  # noqa: E402


def _ns(input_dir, output_dir, work_dir):
    return argparse.Namespace(input=input_dir, output=output_dir, work=work_dir)


def _write(path, content, encoding='utf-8'):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    mode = 'wb' if isinstance(content, bytes) else 'w'
    kwargs = {} if isinstance(content, bytes) else {'encoding': encoding}
    with open(path, mode, **kwargs) as f:
        f.write(content)


def _case_dirs():
    root = tempfile.mkdtemp(prefix='skill-selftest-')
    return (os.path.join(root, 'input'), os.path.join(root, 'output'),
            os.path.join(root, 'work'), root)


def _run_pipeline(input_dir, output_dir, work_dir, scaffold=True):
    """Return (exit_codes, final_result). When scaffold=False, assumes
    plan.json/ledger.json were already filled by a simulated editing pass."""
    codes = []
    if scaffold:
        codes.append(main.scaffold(_ns(input_dir, output_dir, work_dir)))
    codes.append(main.verify(_ns(input_dir, output_dir, work_dir)))
    codes.append(main.finalize(_ns(input_dir, output_dir, work_dir)))
    codes.append(main.selfcheck(_ns(input_dir, output_dir, work_dir)))
    result = main.read_json(os.path.join(output_dir, 'result.json'))
    return codes, result


def test_empty_input():
    i, o, w, root = _case_dirs()
    try:
        _write(os.path.join(i, 'task.md'), '# task\n')
        codes, result = _run_pipeline(i, o, w)
        assert all(c == 0 for c in codes), codes
        assert result['status'] == 'insufficient_evidence', result['status']
        assert os.path.isfile(os.path.join(o, 'result.json'))
        return True, 'empty input -> insufficient_evidence, exit 0'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_json():
    i, o, w, root = _case_dirs()
    try:
        _write(os.path.join(i, 'task.md'), '# task\n')
        _write(os.path.join(i, 'case.json'), '{not valid json', encoding='utf-8')
        codes, result = _run_pipeline(i, o, w)
        assert all(c == 0 for c in codes), codes
        assert result['status'] in ('partial', 'input_error'), result['status']
        assert result.get('errors') or result.get('degradations')
        return True, 'bad case.json -> degraded but no crash'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_bad_encoding():
    i, o, w, root = _case_dirs()
    try:
        _write(os.path.join(i, 'task.md'), '# task\n')
        # GBK-encoded bytes that are invalid UTF-8.
        _write(os.path.join(i, 'materials', 'draft.md'),
               '本底稿含中文：\u9632\u6b62\u88ab\u6539\u574f'.encode('gbk'))
        main.scaffold(_ns(i, o, w))
        plan = main.read_json(os.path.join(w, 'plan.json'))
        assert plan is not None
        return True, 'GBK bytes -> read_text fallback, no crash'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _minimal_fixture(i, o, w):
    _write(os.path.join(i, 'task.md'),
           '# 任务\n请润色以下摘要，保留数字、单位、引用与限定强度。\n')
    _write(os.path.join(i, 'case.json'), json.dumps({
        'case_id': 'synthetic-001',
        'output_contract': {
            'deliverable': 'manuscript.md',
            'fact_source_map': [],
            'version_boundary': [],
            'custom_field': 'keep-me',
        },
    }, ensure_ascii=False))
    draft = ('The result suggest that treatment significantly improve the outcome '
             '(p = 0.03, n = 128), as shown in Fig. 2 and Table 1 [12]. '
             'The change was 3.5 mmHg.')
    _write(os.path.join(i, 'materials', 'draft.md'), draft)


def _fill_intermediates(w, case_id):
    """Simulate the agent's editing pass: write a valid plan + ledger."""
    plan = {
        'schema_version': '1.0',
        'case_id': case_id,
        'target_lang': 'en',
        'genre': 'journal',
        'audience_notes': 'peer-reviewed clinical journal',
        'style_rules': ['minimum edit', 'preserve hedges'],
        'sections': [{'id': 's1', 'locator': 'materials/draft.md', 'kind': 'abstract'}],
        'invariants': [
            {'id': 'inv-1', 'kind': 'unit', 'quote': '3.5 mmHg', 'tokens': ['3.5mmHg'],
             'locator': 'materials/draft.md#L1', 'strength_class': 'hard'},
            {'id': 'inv-2', 'kind': 'citation', 'quote': '[12]', 'tokens': ['[12]'],
             'locator': 'materials/draft.md#L1', 'strength_class': 'hard'},
            {'id': 'inv-3', 'kind': 'figure_ref', 'quote': 'Fig. 2', 'tokens': ['fig.2'],
             'locator': 'materials/draft.md#L1', 'strength_class': 'hard'},
            {'id': 'inv-4', 'kind': 'table_ref', 'quote': 'Table 1', 'tokens': ['table1'],
             'locator': 'materials/draft.md#L1', 'strength_class': 'hard'},
            {'id': 'inv-5', 'kind': 'number', 'quote': '128', 'tokens': ['128'],
             'locator': 'materials/draft.md#L1', 'strength_class': 'hard'},
            {'id': 'inv-6', 'kind': 'hedge', 'quote': 'suggest', 'tokens': [],
             'locator': 'materials/draft.md#L1', 'strength_class': 'moderate'},
        ],
        'terminology': [{'concept': 'treatment', 'canonical': 'treatment',
                         'variants': ['treatement'], 'first_occurrence': 'draft.md#L1'}],
        'gaps': [],
        'conflicts': [],
        'uncertainty': [{'detail': 'synthetic fixture', 'status': 'noted'}],
        'change_log': [{'phase': 'edit', 'action': 'synthetic'}],
    }
    ledger = {
        'schema_version': '1.0',
        'case_id': case_id,
        'units': [{
            'id': 'u-1',
            'locator': 'materials/draft.md#L1',
            'original': 'The result suggest that treatment significantly improve the outcome (p = 0.03, n = 128), as shown in Fig. 2 and Table 1 [12]. The change was 3.5 mmHg.',
            'revised': 'The results suggest that treatment significantly improved the outcome (p = 0.03, n = 128), as shown in Fig. 2 and Table 1 [12]. The change was 3.5 mmHg.',
            'edit_type': 'grammar',
            'reason': 'subject-verb agreement and tense consistency',
            'touched_invariants': ['inv-1', 'inv-2', 'inv-3', 'inv-4', 'inv-5', 'inv-6'],
            'conservation': {
                'inv-6': {'after_class': 'moderate', 'verdict': 'preserved',
                          'detail': 'suggest retained'},
            },
            'requires_human': False,
            'human_note': None,
        }],
    }
    main.write_json(os.path.join(w, 'plan.json'), plan)
    main.write_json(os.path.join(w, 'ledger.json'), ledger)


def test_minimal_valid():
    i, o, w, root = _case_dirs()
    try:
        _minimal_fixture(i, o, w)
        main.scaffold(_ns(i, o, w))
        _fill_intermediates(w, 'synthetic-001')
        codes, result = _run_pipeline(i, o, w, scaffold=False)
        assert all(c == 0 for c in codes), codes
        assert result['status'] == 'ok', result['status']
        assert result['case_id'] == 'synthetic-001'
        assert result['custom_field'] == 'keep-me'
        assert len(result['fact_source_map']) >= 6
        assert os.path.isfile(os.path.join(o, 'manuscript.md'))
        check = main.read_json(os.path.join(w, 'check_report.json'))
        assert check['errors'] == [], check['errors']
        return True, 'minimal valid -> ok, contract keys preserved, 6 facts traced'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_violation_detected():
    i, o, w, root = _case_dirs()
    try:
        _minimal_fixture(i, o, w)
        main.scaffold(_ns(i, o, w))
        _fill_intermediates(w, 'synthetic-001')
        # Corrupt one unit: change the number in the revised sentence only.
        ledger = main.read_json(os.path.join(w, 'ledger.json'))
        ledger['units'][0]['revised'] = (
            'The results suggest that treatment significantly improved the outcome '
            '(p = 0.03, n = 128), as shown in Fig. 2 and Table 1 [12]. '
            'The change was 4.5 mmHg.')
        main.write_json(os.path.join(w, 'ledger.json'), ledger)
        main.verify(_ns(i, o, w))
        check = main.read_json(os.path.join(w, 'check_report.json'))
        assert any(e.get('invariant_id') == 'inv-1' for e in check['errors']), check
        return True, 'deliberately altered 3.5 mmHg -> checker reports error'
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_invariant_engine():
    cands = invariants.extract_hard_candidates(
        'The pressure was 3.5 mmHg [12] (Fig. 2); n=128.')
    kinds = {c['kind'] for c in cands}
    assert 'unit' in kinds and 'citation' in kinds and 'figure_ref' in kinds, kinds
    assert invariants.classify_hedge('The data suggest a benefit') == 'moderate'
    assert invariants.classify_hedge('The data prove a benefit') == 'absolute'
    assert invariants.norm('3.5 mmHg') == invariants.norm('3.5mmHg')
    return True, 'invariant engine heuristics behave as documented'


def main_selftest():
    tests = [
        test_empty_input,
        test_bad_json,
        test_bad_encoding,
        test_minimal_valid,
        test_violation_detected,
        test_invariant_engine,
    ]
    failures = 0
    for t in tests:
        try:
            ok, msg = t()
        except Exception as e:
            ok, msg = False, 'EXCEPTION: %s' % e
        print('[%s] %s : %s' % ('PASS' if ok else 'FAIL', t.__name__, msg))
        if not ok:
            failures += 1
    print('selftest %s (%d/%d passed)' % ('OK' if failures == 0 else 'FAILED',
                                           len(tests) - failures, len(tests)))
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main_selftest())
