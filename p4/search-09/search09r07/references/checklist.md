# 自检清单（对应评分维度）

交付前逐条勾选；失败项写入 `result.json` 的 `errors` / `degradations` /
`limitations`，不得删改掩盖。脚本自检用 `python3 -B scripts/selftest.py`。

## A. 实际效果（40 分）

- [ ] 产出“维度—判断—证据—等级”完整评价表（`assessment`），非摘要换说法。
- [ ] 每条判断与原文一致；研究设计类型与统计口径识别正确。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`python3 -B scripts/main.py verify` 抽查通过）。
- [ ] 覆盖研究设计、证据充分性、偏倚风险、报告完整性四类维度 + 文献类型特有检查点。
- [ ] 第二人按 `locator + quote` 可独立复核并得出可比结论（verdict 受限取值）。

## B. 使用增益（25 分）

- [ ] 量表驱动（`references/rubric.md`），维度覆盖优于泛化摘要。
- [ ] 弃权规则避免逐条补缺，单篇人工复核条目数下降。
- [ ] 同量表、双人复核可比（字段结构统一、判定值受限、分级阈值可解释）。

## C. 创新增益（15 分）

- [ ] 结构化能力：量表驱动、证据锚定、分级可解释、缺证据可弃权。
- [ ] 量表映射、证据优先级、分级阈值、弃权规则有实质改进且文档化（rubric.md §3–§5）。
- [ ] 说明相对通用摘要 / 公开报告清单 / 编辑初筛的边界与价值。

## D. 可靠体验（10 分）

- [ ] `python3 -B scripts/main.py skeleton` 可跑通；`self-test` 退出码 0。
- [ ] 不同研究类型、不同篇幅、重复运行持续产出可用评价表（确定性、无随机数）。
- [ ] 已处置：全文不可得 / 仅摘要 / 图表不可解析 / 语言或预印本特殊 / 关键证据缺失。

## E. 生态贡献（10 分）

- [ ] 量表定义、维度含义、分级阈值、适用文献类型、依赖与限制齐全（rubric.md）。
- [ ] 量表、脚本、证据定位模板可被他人直接复用。
- [ ] 沉淀可分享的评价案例、失败经验与真实应用价值说明。

## F. 运行产物完整性

- [ ] 前 3 分钟内已用 `skeleton` 落盘 `result.json` 骨架。
- [ ] `result.json` 含 `case_id`、题目 `output_contract` 全部字段（原样回填，缺值 null/[]）、
      `status`、`sources`、`uncertainty`、`change_log`。
- [ ] 主产物 JSON 可解析；可编辑交付件（md）已生成。
- [ ] `status` 诚实：`ok | partial | insufficient_evidence | input_error`，不编造。
- [ ] 最后跑过 `python3 -B scripts/main.py validate` 且无 error 级问题。

## G. 异常 → 处置 → 字段 对照表

| 异常 | 处置 | 落到 result.json |
|-|-|-|
| 空输入目录 | 写骨架，case_id=unknown | `status=insufficient_evidence`，`errors` 记 no_case_json |
| 输入目录不存在 | 写骨架 | `status=input_error`，`errors` 记 input_dir_missing |
| 坏 JSON | 解析失败继续处理其余材料 | `errors` 记 bad_json，`status=input_error` |
| 坏编码 | latin-1 兜底解码 | `degradations` 记 decode 失败 |
| 超大文件 >2MB | 跳过该文件 | `degradations` 记 oversize |
| 缺参数 | 用 env/缺省 `/input` `/app/output` `/app/work` | 无需记录，行为确定 |
| 写盘失败 | 降级 work_dir，再兜底当前目录 | `degradations`/`errors` 记路径 |
| 全文不可得/仅摘要 | 降级评价 | `limitations` + `basis=abstract_only` |
| 图表不可解析 | 不推断数值 | `degradations` + 条目判 insufficient_evidence |
| 冲突信息 | 全部记录 | 标 `source_conflict` + `uncertainty` |
| 缺失元数据/权限 | 如实说明 | `omissions`/`pending_human` |

## H. 运行顺序参考（对应 SKILL.md §4）

1. `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`
2. `python3 -B scripts/main.py locators --text-file <txt> --source-id <id> --out <json>`
3. 逐条评价并 `python3 -B scripts/main.py verify --locators <json> --locator <s#p1> --quote "<q>"`
4. `python3 -B scripts/main.py grade --assessments <json>`
5. `python3 -B scripts/main.py validate --input /input --output /app/output`
6. `python3 -B scripts/selftest.py`（提交前，退出码须为 0）
