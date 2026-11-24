# 定位规范、自检清单与最小调用示例

## 1. locator 定位规范

统一格式 `S{section}.P{para}.s{sentence}`：

- `S{section}`：章节（用材料标题或序号）；无法分节时省略，退化为 `P{para}.s{sentence}`。
- `P{para}`：段落序号（从 1 开始，按材料内顺序）。
- `s{sentence}`：句序号（从 1 开始）。
- 找不到句子级时可用 `S{section}.P{para}` 或字符区间 `[start:end]`（字符指材料原文偏移）。
- 每条判断必须能凭 `source_doc + locator + quote` 回到原文逐字复核。

## 2. 自检清单（提交前逐条勾选，对应评分维度）

实际效果
- [ ] 产出"论断清单—证据表—成稿"三段交付，缺一不可。
- [ ] 每条论断可独立判定真伪，并指回至少一个来源。
- [ ] 引用真实存在（元数据与原文一致），原文确支持该论断（非相近但更弱/更强的话）。
- [ ] 论断、来源、证据强度标注、不确定性表述四类要素齐备。
- [ ] 每条来源含 source_doc + locator + 逐字 quote，可被他人独立回查。

使用增益
- [ ] 相比"仅给成稿"，额外提供证据表与论断编号，减少回原文核对返工。
- [ ] 过度推断段落已按口径改写弱化或删除，而非保留。
- [ ] 同稿同口径记录在 sources/change_log，可支撑增益核验。

创新增益
- [ ] 含论断切分 + 来源匹配 + 证据强度定级 + 表述校准的完整方法链。
- [ ] S1–S4 分级与 support_scope 口径可独立复用（见 01_evidence_strength.md）。
- [ ] 创新点边界写明：只做证据支撑质量，不做自动文献检索/DOI 补全。

可靠体验
- [ ] 前 3 分钟先落盘 status=in_progress 骨架（scaffold.py），之后只做增量改进。
- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/截断/权限不足均写入 limitations。
- [ ] 原文与摘要矛盾、证据冲突、二手转引、版本差异均写入 uncertainty。
- [ ] 重复运行不覆盖已 finalize 的 result.json（scaffold 默认不覆盖非 in_progress）。

生态贡献
- [ ] SKILL.md、scripts、references 齐备；依赖（仅 Python 标准库 + pdftotext 可选）、
      路径、限制写清；最小示例标注为示例。

## 3. 最小调用示例（示意输出，非正式案例结果）

```bash
# 1) P0：早落盘骨架（前 3 分钟内）
python scripts/scaffold.py --input /input --output /app/output --work /app/work

# 2) P1/P2：材料归一 + 定位切分 + 证据原子抽取
python scripts/evidence.py --input /input --output /app/output --work /app/work

# 3) Agent 依据 analysis.json / evidence_template.json 填表并写终版 result.json，
#    然后运行自检（P3）
python scripts/selftest.py --output /app/output --render
```

示意 `result.json` 顶层结构（字段值均为示例）：

```json
{
  "case_id": "example-case-01",
  "status": "partial",
  "deliverable": {"artifact_path": "evidence_writing.md", "claims": [], "summary": "示例"},
  "sources": [{"id": "S1", "source_doc": "materials/paper.pdf", "locator": "S3.P2.s1", "quote": "示例原文"}],
  "uncertainty": [], "pending_human": [], "limitations": [], "change_log": []
}
```

> 示例输出仅说明接口；正式评测不使用本示例，参赛者不得提交预先生成的正式答案。
