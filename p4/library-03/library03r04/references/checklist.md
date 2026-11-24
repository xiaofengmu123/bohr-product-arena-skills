# 自查与验收清单（对应评分维度）

交付前逐条执行；`[x]` 表示已核验。脚本 `scripts/selftest.py` 自动覆盖 A/C 组，
其余为人工复核项。

## A. 运行正确性（可靠体验·调用易用/运行稳定）

- [ ] `python3 -B scripts/main.py --help` 退出码 0，参数说明含 `--input/--output/--work/--hierarchy/--apply`。
- [ ] 空输入目录：退出码 0，`status == "insufficient_evidence"`。
- [ ] 缺输入目录：退出码 0，`status == "input_error"`。
- [ ] 坏 `case.json`：不崩溃，`degradations` 记录，契约回退默认，仍产出合法 result.json。
- [ ] 同一输入连跑两次（`--apply`），`catalog/operations/failures` 除时间戳外逐字节一致。
- [ ] `--apply` 后再 `--apply`：同内容目标 `already_present`，不重复复制、不覆盖。

## B. 结果完备性（实际效果·完整性/可核验性）

- [ ] `result.json` 可 `json.load`，含 `case_id` + 契约字段（library/evidence/operations/failures）
      + `status/sources/uncertainty/limitations/change_log/pending_human`。
- [ ] `library.catalog` 覆盖 `inventory.json` 的每个文件（含 `case.json`/`task.md` 输入上下文）。
- [ ] 六类要素逐一交代：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 每个关键结论有 `locator + quote`；哈希与 `evidence.hashes` 一致；冲突两处都记录。

## C. 正确性与诚实性（实际效果·正确性；降级与不确定性）

- [ ] 元数据只来自 BibTeX/RIS/PDF 元数据/正文显式片段/文件名；缺失字段 `null`+`not_reported`。
- [ ] `field_basis` 标注每个字段证据强度：`explicit/inferred/not_reported`。
- [ ] `uncertainty.counts` 给出：无文本层数、推断分类数、各元数据字段未报告数。
- [ ] `limitations` 显式说明：OCR 不执行、命名降级、工具缺失、版本判定依据、复制而非移动。
- [ ] 扫描件（无文本层）标 `has_text_layer=false`，语义元数据不臆测。
- [ ] 命名冲突 `-N` 后缀解决；未把 `dry_run`/`plan` 写成 `copied`；`copy_failed` 如实入 `skipped`。

## D. 增益可验证（使用增益·创新增益）

- [ ] 整理前后对照：`/input` 原样未动，`/app/output/library/` 是实际归位结果。
- [ ] `rollback.json` 列出本次创建的全部目标文件，删除即可回滚。
- [ ] 命名模板、标签归一、哈希去重、版本聚合规则在 `references/rules.md` 可复用。

## E. 失败场景→字段映射（可靠体验·异常处理）

| 场景 | 应写入 | status 影响 |
|---|---|---|
| 不可读文件 / stat 失败 | `failures.permission_errors` + `skipped` + 条目 `errors` | partial |
| 坏编码（替换字符） | 条目 `limitations` + `degradations` | partial |
| 加密 PDF / 无文本层扫描件 | `has_text_layer=false` + 条目 `limitations` | 视结果 |
| 同名不同版本 | `failures.conflicts` + `operations` 后缀日志 | 不误删 |
| 权限不足 | `evidence.permissions` 记录 `readable/writable` | partial |
| 缺 pdftotext/pdfinfo | `degradations`，保留清单与哈希 | partial |
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
assert 'sources' in r and 'uncertainty' in r and 'change_log' in r and 'limitations' in r
print('ok', r['status'], r['case_id'])
PY
```

> 示例仅验证接口，不冒充正式案例结果；正式结果以平台固定案例实际运行为准。
