# 检查清单与降级矩阵（checklist）

## 1. 逐方向检查清单（无 case 自带 facets 时的默认项）

- [ ] **学术诚信**：数据/图像是否复用、是否重复发表、署名是否真实、利益冲突声明、
  致谢与基金信息是否一致。
- [ ] **引用规范**：被引文献是否真实存在、元数据是否一致、正文论断与所引文献是否一一对应、
  是否引而不证或张冠李戴、格式是否统一。
- [ ] **隐私与数据合规**：是否有可识别个人信息/受限数据/未公开数据，是否具备授权或伦理依据，
  是否需要脱敏。
- [ ] **版权与许可**：图/表/代码/数据集的来源标注、许可类型、复用条件是否清楚，需补充授权的位置。
- [ ] **伦理审查**：涉人研究是否说明知情同意与伦理批件编号及适用范围，动物实验是否规范，
  风险/获益表述是否与实际设计一致。
- [ ] **投稿要求**：目标期刊/会议指南是否拆成可核对条目并逐项映射，缺失/超限/格式冲突是否报告，
  依据的指南版本与日期是否注明。
- [ ] **通用质量/事实/方法/统计/逻辑**：表述与结构、数值一致性、方法完整性、统计表述、
  论证链是否成立。

若 `case.json` 提供了 `review_facets` / `facets` / `checklist`，则以 case 自带 facet 为准，
逐项映射，而不是套用默认清单。每个 facet 都要在 `report.facet_coverage` 中给出状态
（`ok / issue / pending`）与问题数。

## 2. 自检清单（交付/运行前逐条勾选）

- [ ] `result.json` 可解析，`case_id` 非空
- [ ] `status` 诚实且 ∈ {ok, partial, insufficient_evidence, input_error}
- [ ] `output_contract` 全部字段存在（缺值 null/[]，未改名未删键），并平铺叶子键到顶层
- [ ] `sources` / `uncertainty` / `change_log` 均为数组且非空
- [ ] 每条 finding 含 `id/type/severity/category/title/file/locator/quote/evidence/basis/revision`
- [ ] 每条 `quote` 能回查原文（`quote_verified: true`，validate.py 校验通过）
- [ ] 类型四分类与严重度分级正确，误报已排除或降级
- [ ] 各检查方向逐项有"已查/待复核/未查"结论，未只挑最容易的一条
- [ ] 材料缺失/解析失败/软件缺失已写入 `errors`/`degradations`/`pending_human`
- [ ] `report.md` 存在可读，`result.json` 记录其路径
- [ ] `change_log` 记录每次增量改动
- [ ] 同一输入重跑结论基本一致（脚本确定性、规则固定）

运行：`python3 -B scripts/validate.py` 校验；`python3 -B scripts/validate.py selftest`
跑空输入/坏 JSON/坏编码/完整链路四场景。

## 3. 降级矩阵

| 场景 | 处置 |
|-|-|
| 空输入目录 | 退出码 0，`status=insufficient_evidence`，不抛异常 |
| case.json 缺失/坏 JSON | 记录 `errors`，用默认 facets 继续，`status=partial` |
| 材料坏编码 | 多编码降级解码（utf-8-sig → gb18030 → big5 → latin-1），记录 `degradations` |
| 单文件 > 2MB | 跳过并记录 `degradations` |
| PDF 无文本层 | 依次 pdf-text-tool → pdftotext → OCR；仍不可得标 `insufficient_evidence`/`partial` |
| 缺 `output_contract` | 用标准 schema，标 `contract_missing=true` |
| 缺软件/工具 | 如实标 limitations，不伪造运行结果、评测分数、软件状态 |
| 受限数据 | 只标位置与类型，不复述不扩散，拿不准标 `pending_human` |

## 4. 输出字段总览

`result.json`：`case_id`、`status`、`output_contract`（全字段）、叶子字段平铺、`report`
（summary/scope/facets/facet_coverage/findings/checked_items/pending_review/not_checked）、
`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、
`runtime`、`result_path`、`report_path`、`input_fingerprint`。
