# 自查与验收清单（checklist.md）

交付前逐条执行；`[x]` 表示已核验。括号内标注对应评分维度。

## A. 运行正确性（可靠体验·调用易用/稳定）

- [ ] `python3 -B scripts/main.py --help` 退出码 0，参数说明完整。
- [ ] 缺输入目录：退出码 0，`result.json.status == "input_error"`。
- [ ] 空输入目录：退出码 0，`status == "insufficient_evidence"`。
- [ ] 坏 `case.json`：不崩溃，`degradations` 记录契约回退，继续处理材料。
- [ ] 同一输入连跑两次：`catalog`/`operations`/`versions` 除时间戳外逐字节一致（幂等）。
- [ ] `--apply` 后再次 `--apply`：同名同内容不重复复制（`already_present`）、不覆盖；不同内容 `-N` 后缀。

## B. 结果完备性（实际效果·完整性/可核验性）

- [ ] `result.json` 可被 `json.load` 解析，含 `case_id` + 契约全部字段 + `status`/`sources`/`uncertainty`/`change_log`。
- [ ] `library.catalog` 覆盖 `inventory.json` 的**每个**文件（含输入规格）；六类要素（文件类型/元数据/目录层级/重复/版本/权限与来源）逐一交代。
- [ ] 每个关键结论有 `locator + quote`；哈希与 `evidence.hashes` 一致。
- [ ] 冲突信息两处都记录（`source_conflict`/`conflicts[]`），未隐藏。

## C. 正确性与诚实性（实际效果·正确性；降级）

- [ ] 元数据只来自原文/BibTeX/RIS/JSON/PDF 内嵌信息；未提取字段为 `null`/`not_reported`，无凭空作者/年份/DOI。
- [ ] 字节级重复经 SHA-256 判定；版本组经 DOI/arXiv/标题+年份 聚合，角色有标记依据。
- [ ] 扫描件（无文本层）标 `has_text_layer=false`，元数据不臆测。
- [ ] 命名冲突以 `-N` 后缀解决，日志可定位；未把 `dry-run` 写成 `copied`。

## D. 中间表示与增益可验证（使用增益·创新增益）

- [ ] `work/inventory.json`、`decisions.json`、`plan.json`、`rollback.json` 四层产物齐备。
- [ ] `plan.json` 落地前可审计：每条含源/类别/新名/动作/去重/版本/证据。
- [ ] `/input` 原样未动；`/app/output/library/` 是实际归位结果；`rollback.json` 删除即可回滚。
- [ ] 命名模板、标签归一、哈希去重规则在 `references/rules.md` 可复用，非硬编码。

## E. 失败场景→字段映射（可靠体验·异常处理）

| 场景 | 应写入 | status 影响 |
|---|---|---|
| 不可读文件 / stat 失败 | `failures.permission_errors` + `errors` | partial |
| 坏编码（替换字符） | 文件条目 `encoding` + `degradations` | partial |
| 加密 PDF / 无文本层扫描件 | `has_text_layer=false` + `uncertainty.no_text_layer` | partial |
| 中文/多语言乱码 | `degradations`，改用 latin-1/replace 重读并标注 | 视结果 |
| 同名不同版本 | `failures.conflicts` + `operations` 后缀日志 | 不误删 |
| 权限不足 | `evidence.permissions` 记录 `readable/writable` | partial |
| 缺 pdftotext/pdfinfo | `degradations`，保留清单与哈希 | 不影响清单 |
| decisions 无证据 | `pending_human`，字段不采纳 | partial |

## F. 最小验收命令

```bash
cd /skills/weekly-skill
python3 -B scripts/selftest.py
python3 -B scripts/main.py --input /input --output /app/output --work /app/work --apply
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('case_id') and r.get('status') in {'ok','partial','insufficient_evidence','input_error'}
assert 'sources' in r and 'uncertainty' in r and 'change_log' in r
print('ok', r['status'], r['case_id'])
PY
```

> 说明：上述仅验证接口与中间产物，不冒充正式案例结果；正式结果以平台固定案例实际运行为准。
