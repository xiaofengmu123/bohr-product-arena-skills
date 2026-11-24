# 自查与验收清单（对应评分维度）

交付前逐条执行；`[x]` 表示已核验。括号内标注对应评分维度与分值来源。

## A. 实际效果（40 分：完成度/正确性/完整性/可核验性）

- [ ] `library.catalog` 覆盖输入批次**全部文件**，一个不漏。
- [ ] 六类要素逐一交代：文件类型、元数据字段、目录层级、重复文件、版本差异、
      权限与来源。
- [ ] 目录归属、标签、文件名、去重结论均可用 `evidence.locator+quote` 或 `sha256` 复核。
- [ ] 缺失字段写 `null`/`not_reported`，不凭空补作者/年份/DOI。
- [ ] 冲突信息两处都记录（`source_conflict`），未隐藏。

## B. 使用增益（25 分：质量/效率/增益可验证）

- [ ] 同一输入重跑两次，`library.catalog` 与 `operations` 除时间戳外逐字节一致。
- [ ] `--apply` 后再次 `--apply`：已存在的同内容目标标记 `already_present`，不重复复制。
- [ ] `/input` 原样未动，`/app/output/library/` 是实际归位结果，前后可对照。

## C. 创新增益（15 分：新增能力/方法改进/创新证据）

- [ ] 命名模板、标签归一、哈希去重、冲突合并规则在 `references/rules.md` 可复用，
      非硬编码。
- [ ] 版本组由 DOI/arXiv/标题+年份并查集聚合，`preprint/published` 有标记依据。
- [ ] 大批量幂等、可回滚、失败可续跑（`rollback.json` 列出本次创建文件）。

## D. 可靠体验（10 分：调用易用/运行稳定/异常处理）

- [ ] `python3 -B scripts/main.py --help` 退出码 0。
- [ ] 空输入 → `insufficient_evidence`；缺输入 → `input_error`；均退出码 0、不抛异常。
- [ ] 坏 `case.json` → `degradations`，契约回退默认，继续处理其余材料。
- [ ] 不可读文件/stat 失败 → `failures.permission_errors` + `errors`。
- [ ] 加密 PDF/扫描件无文本层 → `has_text_layer=false` + `failures.skipped`。
- [ ] 同名不同版本 → `failures.conflicts` + `-N` 后缀日志，不误删。
- [ ] 缺 `pdftotext`/OCR → `degradations`，保留清单与哈希。

## E. 生态贡献（10 分：文档/复用/分享）

- [ ] `SKILL.md` 写清用途、输入输出契约、调用方式、异常处理、边界。
- [ ] 规则表、命名模板、标签词表、字段映射、自检脚本齐备，他人可按说明独立使用。
- [ ] `scripts/selftest.py` 可独立运行并返回非零退出码指示失败。

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

> 说明：以上命令验证接口，不冒充正式案例结果；正式结果以平台固定案例实际运行为准。
