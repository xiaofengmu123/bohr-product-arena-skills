# 自检清单、降级与合同回填细则

## 1. 交付前自检（对应评分维度）

**实际效果（40 分）**
- [ ] `manuscript.md` 四段齐全（研究背景、相关工作、研究空白、贡献）且承接，可直接进入论文。
- [ ] 他人工作转述与原文一致，结论/适用条件/局限均保留，引用元数据真实。
- [ ] 空白由已引文献缺口推出，贡献与实际结果相符，不夸大范围。
- [ ] 背景、相关工作、空白、贡献、引用覆盖五要素齐全；主要方法路线、关键工作、必要字段（作者/年份/来源）已覆盖。
- [ ] 每条引用可回到来源；分组依据（plan.json `grouping_scheme`）、空白推导（`gap.basis`）、贡献对照（`contributions[].evidence`）可独立复核。

**使用增益（25 分）**
- [ ] 成稿先落 plan.json 再写正文，避免"边写边想"导致的论断漂移与返工。
- [ ] 用 evidence_map + `[S#]` 把"逐条核对引用"从逐句人工比对降为按表抽查。
- [ ] 中途异常可恢复：骨架先落盘，之后只做增量，不重写已核对内容。

**创新增益（15 分）**
- [ ] 结构化中间表示（plan.json）把"计划"与"成稿"分离，评审可只看计划判断分组/空白/贡献是否成立。
- [ ] 引用与论断绑定、分组比较、空白推导、贡献—证据对齐均落为可机读字段，而非散落在散文里。

**可靠体验（10 分）**
- [ ] 空输入目录：`scripts/main.py skeleton` 退出码 0，写出 `insufficient_evidence`。
- [ ] 坏 JSON / 坏编码：记录到 `errors` / `degradations`，其余材料继续处理。
- [ ] 缺参数：脚本缺省指向 `/input`、`/app/output`、`/app/work`。
- [ ] 重复运行：两次结果一致（无随机；`runtime`/`at` 时间戳除外）。

**生态贡献（10 分）**
- [ ] SKILL.md 说明用途、适用条件、调用方式、输入输出、异常处理、维护信息齐全。
- [ ] `scripts/` 纯标准库可复用；`plan.json` 模板、evidence_map 格式、checklist 可被他人直接套用。
- [ ] 最小调用示例（scripts/main.py --help、selftest）已给出，且标注为示例而非正式案例。

## 2. result.json 合同回填细则

- 合同字段以 case.json 为准，按以下优先级发现：`output_contract` → `output` → `expected_output` → `fields` → `required_fields`；`scan.py`（`main.py scan`）做最佳努力抽取。
- 字段名为字符串则作为 result.json 顶层键，值缺省 `null`；为对象则按对象子键结构回填；为列表则回填列表（`[]`）。
- 保留键（`case_id`、`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`、`artifacts`）为运行时契约必需；合同字段与之重名时，`case_id`/`status` 等强制键保留标准语义，其余冲突记入 `errors` 的 `field_conflicts`。
- Agent 必须读 `work/case_normalized.json` 核实抽取结果，缺失的合同字段手工补进 result.json；`contract_source` 为 `null` 时说明未找到契约键，按 `input_error` 处理并列出已知字段。
- `status` 判定：四段齐全 + 证据齐全 → `ok`；有可用稿但存缺口 → `partial`；材料不足以成稿 → `insufficient_evidence`；输入无法解析 → `input_error`。

## 3. 降级处置表

| 场景 | 处置 |
|---|---|
| 材料不足 / 文献库为空 | `insufficient_evidence`；正文只写可支撑内容，其余标 `MATERIAL GAP` |
| 引用查不到原文 | `pending_human` + `uncertainty`，不臆造元数据 |
| 目标期刊格式未知 | 作者—年份 + `uncertainty` 声明假设 |
| 语言切换 | 术语/数字/引用原位，仅换语，限定强度不变 |
| 单文件 >2MB | 跳过并记 `degradations`，用其余材料继续 |
| PDF/软件不可用 | `degradations` 记录，用现有文本继续，不伪造缺失状态 |
| 写盘失败 | 最小合法 result.json（`input_error` + `errors`）收尾 |

## 4. 最小调用示例（示例，非正式案例结果）

```bash
# 本地演示（目录为示例，非正式案例）
python3 -B scripts/main.py scan      --input ./demo/input --work ./demo/work
python3 -B scripts/main.py skeleton  --work ./demo/work --output ./demo/output
# Agent 在两次脚本之间：读材料 → 填 plan.json → 建 facts.md → 写 manuscript/evidence_map/change_log → 回填 result.json
python3 -B scripts/main.py validate  --work ./demo/work --output ./demo/output
python3 -B scripts/selftest.py
```

示意输出（标明为示例，不冒充正式案例）：

```json
{"case_id": "demo-003", "status": "partial",
 "contract_fields": ["introduction", "related_work", "references"],
 "sources": [{"key": "S1", "locator": "materials/paper.md#p2",
              "quote": "示例：材料逐字片段（此处为演示，非真实数据）", "kind": "fact"}]}
```

## 5. 维护信息

- 适用：中英文科研论文引言与相关工作的起草、改写、补引用、核对。
- 支持引用格式：案例指定格式优先；缺省作者—年份，可改数字式（改动记 change_log）。
- 限制：不做期刊排版模板适配；不生成材料外事实；不自动下载或联网补参考文献。
- 依赖：仅 Python 3.9+ 标准库，无第三方包。

## 6. 运行时逐步核对（每次案例按序执行）

| 阶段 | 必做动作 | 通过标准 |
|---|---|---|
| 扫描后 | 读 `work/case_normalized.json`，确认 `case_id`、`contract_fields`、`contract_source` | 契约字段与 case.json 一致，无遗漏 |
| 骨架后 | 确认 `/app/output/result.json` 存在且可 `json.load` | 前 3 分钟内已落盘，`status` 诚实 |
| 填计划后 | 检查 `grouping_scheme` 每条有 `criterion/works/comparison`；`gap.basis` 非空 | 分组、空白都可复核 |
| 起草前 | 核对 `evidence_budget` 的 `[S#]` 与 facts 一一对应 | 每个事实句有来源预算 |
| 起草后 | 逐条核对数字/单位/限定词/引用元数据 | 无数字漂移、无臆造 |
| 回填后 | result.json 合同字段全部非空（或明示 `null`），`sources` 与 evidence_map 一致 | 契约完整 |
| 封存前 | `scripts/main.py validate` 退出码 0 | 所有产物存在且可解析 |

## 7. 最小可回溯示例（合成，标注示例，非正式案例）

输入 `case.json` 含：

```json
{"case_id": "demo-003",
 "output_contract": {"introduction": null, "related_work": {"groups": [], "citations": []}}}
```

`scan` 后 `case_normalized.json` 的 `contract_fields` 应为 `["introduction", "related_work"]`，`contract_source` 为 `output_contract`。`skeleton` 后 result.json 应含 `introduction: null`、`related_work: {"groups": [], "citations": []}`、`status: "partial"`、`sources: []`、`change_log: []`。

Agent 起草后，`related_work.groups` 回填真实分组，`introduction` 回填成稿文本；`sources` 每项带 locator+quote；`change_log` 记录关键改写；`validate` 通过后 `status` 取 `ok`。示例仅演示契约回填形状，不冒充正式案例结果。

## 8. 引用格式速查（案例优先，缺省作者—年份）

| 情形 | 处理 |
|---|---|
| 案例指定格式（如数字式 `[1]`） | 严格遵循，不擅自改 |
| 未指定 | 作者—年份，如 `Smith et al., 2024` |
| 需改数字式 | 允许，但 change_log 记录改动，`uncertainty` 声明假设 |
| 材料只有摘要/无作者年份 | 写 `pending_human`，不补元数据 |
| 同一作者多年份 | 用材料中真实年份区分，不猜 a/b 后缀 |
| 中英双语 | 引用标记与编号两版共用，避免漂移 |

引用元数据只抄材料中出现的内容；DOI、卷、页、出版社在材料缺失时一律 `pending_human`，不联网补全、不凭记忆补写。

## 9. 评分维度 → 本 Skill 机制对照（Agent 每次案例对照执行）

| 评分维度（分值） | 本 Skill 的具体机制 |
|---|---|
| 任务完成度 / 结果正确性 / 完整性（40 分核心） | 四段强制齐全 + plan.json 分组/空白/贡献 + evidence_map 逐条回溯 |
| 结果可核验性（40 分内 5 分） | 四层回溯：manuscript → evidence_map → plan.json → 来源材料 |
| 质量增益（15 分） | 先计划后成稿，减少论断漂移；分组方案先复核再写正文 |
| 效率增益（5 分） | `[S#]` + evidence_map 把逐句核对降为按表抽查；work/ 缓存避免重读 PDF |
| 增益可验证性（5 分） | change_log + decision_log 记录每处取舍，可重跑对照 |
| 新增能力（5 分） | plan.json 中间表示：引用—论断绑定、分组比较、空白推导、贡献—证据对齐均为机读字段 |
| 方法改进（5 分） | 写作与核对分离（facts → plan → prose → validate 四阶段） |
| 调用易用性（3 分） | SKILL.md 明确触发词与不适用场景，脚本缺省路径可零参数运行 |
| 运行稳定性（4 分） | 前 3 分钟落骨架；坏 JSON/坏编码/空输入/超限文件均降级不崩溃 |
| 异常处理（3 分） | 八类降级处置表；`insufficient_evidence`/`input_error` 诚实取值 |
| 文档完整性 / 复用性（7 分） | SKILL.md + 2 份 reference + 可复用模板（§10）与自检脚本 |

执行顺序：先按第 1 节逐条自检（实际效果与可靠体验），再按上表确认每个评分维度都有对应机制落地，最后跑 `validate` 与 `selftest` 封存。
