#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：用合成的临时夹具跑通 骨架→证据→fragment→收尾，断言输出契约。

夹具内容为自检专用，标注为合成样例，不代表任何正式案例，也不随包提交答案。
"""
import json
import os
import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl  # noqa: E402


def _check(cond, msg, failures):
    if cond:
        print('  ok  ' + msg)
    else:
        print('  FAIL ' + msg)
        failures.append(msg)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='skill-selftest-'))
    try:
        inp = tmp / 'input'
        out = tmp / 'output'
        work = tmp / 'work'
        (inp / 'materials').mkdir(parents=True)
        (inp / 'case.json').write_text(json.dumps({
            'case_id': 'selftest-case-000',
            'output_contract': {
                'recommendation': '',
                'feasibility_summary': '',
                'evidence_count': 0,
            },
        }, ensure_ascii=False), encoding='utf-8')
        (inp / 'task.md').write_text(
            '# 自检任务\n\n这是一个合成的自检任务，仅用于验证脚本管线。\n', encoding='utf-8')
        (inp / 'materials' / 'note.md').write_text(
            '## 说明\n\n自检材料第一行。\n自检材料第二行。\n', encoding='utf-8')

        failures = []
        print('[1/4] skeleton')
        rc = core_impl.run_skeleton(str(inp), str(out), str(work))
        r = json.loads((out / 'result.json').read_text(encoding='utf-8'))
        _check(rc == 0, 'skeleton exit 0', failures)
        _check(r.get('case_id') == 'selftest-case-000', 'case_id resolved', failures)
        _check(r.get('status') == 'partial', 'status=partial', failures)
        _check(all(k in r for k in ('recommendation', 'feasibility_summary', 'evidence_count')),
               'contract fields flattened', failures)
        _check(len(r.get('change_log') or []) == 1, 'change_log has 1 entry', failures)

        print('[2/4] evidence')
        core_impl.run_evidence(str(inp), str(out), str(work))
        r = json.loads((out / 'result.json').read_text(encoding='utf-8'))
        _check(any(s['source_id'] == 'materials/note.md' and s.get('text_status') == 'readable'
                   for s in r.get('sources') or []), 'material readable', failures)
        _check((work / 'material_inventory.json').is_file(), 'inventory written', failures)

        print('[3/4] fragment + finalize')
        frag = {
            'research_question': {'testable_statement': '合成语句（自检，非正式案例）'},
            'recommendation': '合成建议（自检，非正式案例）',
            'evidence_matrix': [
                {'source_id': 'materials/note.md', 'locator': 'materials/note.md#L3',
                 'quote': '自检材料第一行。', 'field': 'test', 'status': 'reported'},
            ],
        }
        (work / 'fragments').mkdir(exist_ok=True)
        (work / 'fragments' / '01_question.json').write_text(
            json.dumps(frag, ensure_ascii=False), encoding='utf-8')
        core_impl.run_finalize(str(inp), str(out), str(work))
        r = json.loads((out / 'result.json').read_text(encoding='utf-8'))
        _check(r['contract_alignment']['recommendation'].get('filled') is True,
               'contract field recommendation filled', failures)
        _check(r.get('research_question') is not None, 'research_question merged', failures)
        _check(len(r.get('evidence_matrix') or []) == 1, 'evidence_matrix merged', failures)
        _check((out / 'report.md').is_file(), 'report.md written', failures)
        _check(not core_impl._find_nan(r), 'no NaN', failures)
        _check(r.get('status') in ('ok', 'partial'), 'honest status', failures)

        print('[4/4] result.json schema')
        for k in ('case_id', 'status', 'output_contract', 'contract_alignment',
                  'research_question', 'search_protocol', 'prior_work', 'novelty',
                  'feasibility', 'evidence_matrix', 'conflicts', 'synthesis',
                  'sources', 'uncertainty', 'pending_human', 'errors', 'degradations',
                  'runtime', 'change_log'):
            _check(k in r, 'has key ' + k, failures)

        if failures:
            print('\nSELFTEST FAILED: %d check(s)' % len(failures))
            return 1
        print('\nSELFTEST OK')
        return 0
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
