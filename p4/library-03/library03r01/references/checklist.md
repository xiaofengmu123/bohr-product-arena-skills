# 自查与验收清单

交付前逐条执行并勾选；括号内标注对应评分维度。可用 `python3 -B scripts/selftest.py` 自动化验证 A/E 类。

## A. 运行正确性（可靠体验·调用易用/运行稳定）

- [ ] `python3 -B scripts/main.py --help` 退出码 0，参数说明完整。
- [ ] 空输入目录：退出码 0，`result.json.status == "insufficient_evidence"`。
- [ ] 缺输入目录：退出码 0，`status == "input_error"`。
- [ ] 坏 `case.json`：不崩溃，`degradations` 记录 invalid/unreadable，契约回退默认。
- [ ] 同一输入 + 同一 `decisions.json` 连跑两次，`catalog`/`evidence_map`/`operations` 除时间戳外逐字节一致。
- [ ] `--apply` 后再次 `--apply`：同内容目标标 `already_present`，不重复复制、不覆盖。

## B. 结果完备性（实际效果·完整性/可核验性）

- [ ] `result.json` 可被 `json.load` 解析，含 `case_id` + 契约全字段 + `status`/`sources`/`uncertainty`/`change_log`。
- [ ] `library.catalog` 覆盖 `inventory.json` 里**每个**文件；六类要素（文件类型/元数据/目录层级/重复/版本/权限与来源）均有记录。
- [ ] `evidence_map.claims` 每条都有非空 `locator + quote`；哈希与 `evidence.hashes` 一致。
- [ ] 冲突信息两处都记录（如 `source_conflict`），未隐藏。

## C. 正确性与诚实性（实际效果·正确性；降级）

- [ ] 元数据只来自原文/BibTeX/decisions；未提取字段为 `null`/`not_reported`，无凭空作者/年份/DOI。
- [ ] 字节级重复经 SHA-256 判定；版本组经 DOI/arXiv/标题+年份 聚合；版本角色有标记依据。
- [ ] 扫描件（无文本层）标 `has_text_layer=false`，元数据不臆测。
- [ ] 命名冲突以 `-N` 后缀解决，日志可定位；未把 `planned` 写成 `copied`。

## D. 增益可验证（使用增益·创新增益）

- [ ] 整理前后目录对照：`/input` 原样未动，`/app/output/library/` 是实际归位结果。
- [ ] `rollback.json` 列出本次创建的全部目标文件，删除即可回滚。
- [ ] 命名模板、标签归一、哈希去重、版本聚合、BibTeX 标题匹配、evidence_map schema 在 `references/rules.md` 有可复用说明，非硬编码到单一文件。

## E. 失败场景→字段映射（可靠体验·异常处理）

| 场景 | 应写入 | status 影响 |
|---|---|---|
| 不可读文件 / stat 失败 | `failures.permission_errors` + `errors` | partial |
| 坏编码（替换字符） | 文件条目 `text_errors` + `degradations` | partial |
| 加密 PDF / 无文本层扫描件 | `has_text_layer=false` + `failures.skipped` | partial |
| 中文乱码 | `degradations`，改用 utf-8-sig/latin-1 重读 | 视结果 |
| 同名不同版本 | `failures.conflicts` + `operations` 后缀日志 | 不误删 |
| 权限不足 | `evidence.permissions` 记录 `readable/writable` | partial |
| 缺 pdftotext/OCR | `degradations`，保留清单与哈希 | 不影响清单 |
| decisions 无证据 | `pending_human`，字段不采纳 | partial |

## F. 最小验收命令

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --input /input --output /app/output --work /app/work --apply
python3 -B scripts/selftest.py
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('case_id') and r.get('status') in {'ok','partial','insufficient_evidence','input_error'}
assert 'sources' in r and 'uncertainty' in r and 'change_log' in r
claims = r.get('evidence', {}).get('evidence_map', {}).get('claims', [])
assert claims and all(c.get('locator') and c.get('quote') for c in claims)
print('ok', r['status'], r['case_id'], len(claims), 'claims')
PY
```

> 示例仅验证接口，不冒充正式案例结果；正式结果以平台固定案例实际运行为准。
