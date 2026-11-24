# 自查与验收清单

交付前逐条执行；与评分维度对应关系在括号内标注。`scripts/selftest.py` 可自动覆盖 A–G 的机器可验部分。

## A. 运行正确性（可靠体验·调用易用/运行稳定）

- [ ] `python3 -B scripts/main.py --help` 退出码 0，参数说明完整。
- [ ] 空输入目录：退出码 0，`result.json.status == "insufficient_evidence"`。
- [ ] 缺输入目录：退出码 0，`status == "input_error"`。
- [ ] 坏 `case.json`：不崩溃，`degradations` 记录「case.json unreadable or invalid JSON」，契约回退默认。
- [ ] 同一输入 + 同一 `decisions.json` 连跑两次，`catalog`/`operations` 除时间戳外逐字节一致（幂等）。
- [ ] `--apply` 后再次 `--apply`：同内容目标 `already_present`，不重复复制、不覆盖。

## B. 结果完备性（实际效果·完整性/可核验性）

- [ ] `result.json` 能被 `json.load` 解析，含 `case_id` + 契约全字段 + `status/sources/uncertainty/change_log`。
- [ ] `library.catalog` 覆盖 `work/inventory.json` 里每个 `role=material` 文件；六类要素（文件类型/元数据字段/目录层级/重复/版本/权限与来源）均有记录。
- [ ] 每个关键结论有 `locator + quote` 或 `sha256` 佐证；哈希与 `evidence.hashes` 一致。
- [ ] 冲突两处都记录（`source_conflict`/`-N` 后缀），未隐藏。

## C. 正确性与诚实性（实际效果·正确性；降级）

- [ ] 元数据只来自原文/BibTeX/RIS/逐字语录；未提取字段为 `null`/`not_reported`，无凭空作者/年份/DOI。
- [ ] 字节级重复经 SHA-256 判定；版本组经 DOI/arXiv/标题+年份并查集聚合，`preprint/published` 有标记依据。
- [ ] 扫描件/PDF 无文本层标 `has_text_layer=false`；未抽取标 `null`，不臆测。
- [ ] 命名冲突以 `-N` 后缀解决，日志可定位；`dry-run` 不写 `copied`。

## D. 增益可验证（使用增益·创新增益）

- [ ] `/input` 原样未动；`/app/output/library/` 是实际归位结果。
- [ ] `work/rollback.json` 列出本次创建的全部目标文件，删除即可回滚。
- [ ] 命名模板、标签归一、哈希去重、语录校核规则在 `references/rules.md` 可复用，非硬编码。

## E. 失败场景→字段映射（可靠体验·异常处理）

| 场景 | 应写入 | status 影响 |
|---|---|---|
| 不可读文件 / stat 失败 | `failures.permission_errors` + `errors` | partial |
| 坏编码（替换字符） | 文件条目 `errors` + `degradations` | partial |
| 加密 PDF / 无文本层 | `has_text_layer=false` + `failures.skipped` | partial |
| 中文乱码 | `degradations`，改 utf-8-sig/latin-1 重读 | 视结果 |
| 同名不同版本/不同内容 | `failures.conflicts` + `-N` 后缀日志 | 不误删 |
| 权限不足 | `evidence.permissions` 记 `readable/writable` | partial |
| 缺 pdftotext/OCR | `degradations`，保留清单与哈希 | 不影响清单 |
| decisions 无逐字语录 | `pending_human`，字段不采纳 | partial |

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
print('ok', r['status'], r['case_id'])
PY
```

> 说明：示例仅验证接口，不冒充正式案例结果；正式结果以平台固定案例实际运行为准。
