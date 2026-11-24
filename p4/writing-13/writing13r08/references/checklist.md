# 自检清单、result.json 字段与降级策略

## 1. 逐条自检清单（对应评分维度，交付前逐条勾选）

未勾选即视为未通过，不得把 `status` 写成 `done`。

### 实际效果（40 分）
- [ ] 任务完成度：已按 task.md 指定体裁/受众/格式产出**七要素章节稿 + 要素对照说明**，`artifact.path` 指向真实存在的可编辑文件。
- [ ] 结果正确性：每个数字/术语/引用/图表/关键论断都有 `source_ref` 与逐字 `quote`；`unmapped`/`conflict` 已单独说明；"已完成/拟开展"已分清。
- [ ] 结果完整性：七要素齐全，厚度与任务要求匹配，要素之间衔接；目标/内容/路线/指标一一对应。
- [ ] 结果可核验性：`sources` 列全来源文件且含 SHA256，`change_log` 完整，段落—要素—证据三级对应可回溯。

### 使用增益（25 分）
- [ ] 质量增益：四闸门（立得住/说得准/对得上/查得到）给出的判据可观察、可操作，而非泛化描述。
- [ ] 效率增益：前三分钟已落盘骨架，之后增量改进；台账 + 自检减少返工，`change_log` 体现增量而非推倒重写。
- [ ] 增益可验证性：结论基于材料证据与来源映射，不靠精选案例或自我夸奖。

### 创新增益（15 分）
- [ ] 新增能力：把"要素齐备、论断有据、目标与路线一致"翻译为可观察判据，而非只给总分或范文。
- [ ] 方法改进：七要素锚点、四闸门、来源快照、诚实降级相对通用 rubric 有实质区分。
- [ ] 创新证据：`uncertainty`/`limitations` 写清边界，说明做法在哪类材料下可靠、哪类会失效。

### 可靠体验（10 分）
- [ ] 调用与易用性：SKILL.md + references 足以让一名新评分员独立跑通。
- [ ] 运行稳定性：脚本仅用标准库、路径相对化、重复运行结果可解释一致（`python scripts/main.py selftest` 全 PASS）。
- [ ] 异常处理：题面歧义、材料缺失、超长输入、坏编码、模型拒答均有识别与处置记录。

### 生态贡献（10 分）
- [ ] 文档完整：SKILL.md + `references/elements.md` + `references/checklist.md` 齐全，字段与版本/限制信息完整。
- [ ] 资源可复用：七要素清单、四闸门、快照、台账、自检脚本可被他人直接用于自己的申请书写作。
- [ ] 分享价值：沉淀了建题/评分/复现方法与失败分析，而非一次性答案。

## 2. result.json 字段说明

- `case_id`：输入案例标识（来自 case.json 的 id/case_id/challenge_id，或由脚本生成，不得虚构）。
- `status`：`in_progress` / `done` / `partial` / `failed` / `insufficient_evidence` / `input_error`。时间不足或材料缺失一律 `partial`，禁止把未完成写成 `done`。
- `artifact`：主产物。`path` 指向 `/app/output` 下可编辑 `.md`/`.tex`/`.docx`；Office 类需另附真实渲染的 PDF/PNG 预览并在 `path` 或 `sources` 注明。
- `fact_source_map`：事实与来源映射数组，每条含 `claim`、`kind`、`element`、`source_ref`、`locator`、`quote`、`tense`、`status`。字段含义见 `references/elements.md`。
- `version_boundary`：`modification_diff`（修改差异）、`manual_confirm`（需人工确认项）、`material_gaps`（材料缺口）、`anomalies`（异常）。
- `sources`：真实使用到的来源文件清单（路径、SHA256、大小、类型）。
- `uncertainty`：不确定项、推断项、缺数据项，逐条说明原因。
- `pending_human`：需人工确认的事项清单。
- `change_log`：逐次修改记录（时间/动作/原因/影响）。
- `errors`：读取/解析过程中捕获的错误，便于第三方判断降级原因。
- `quality_gates`：四闸门检查报告（七要素覆盖、不可核验修饰词、一致性矩阵）。

**题目 output_contract 回填**：若 case.json 显式给出 `output_contract`/`output_schema`/`required_fields`，`scripts/core_impl.py::build_skeleton` 会把全部键**原样并入** result.json，缺值给 `null`/`[]`/`{}`，不改名、不删除未知键。题目字段优先级最高。

## 3. 降级策略（必须遵守）

| 场景 | 处置 |
|-|-|
| 缺数据/缺材料 | `version_boundary.material_gaps` + `uncertainty` 如实标注，`status=partial`；绝不补造缺失内容、分数、引用或运行结果 |
| 缺软件（PDF/OCR/LaTeX/Office 渲染不可用） | `uncertainty` 写明"未能生成渲染预览"，保留纯文本/Markdown 可编辑中间产物，不伪造预览 |
| 材料超长/格式错乱 | 分段读取并记录已读范围；未读部分写入 `material_gaps`，不得声称已全部核对（脚本单文件 2MB 上限自动跳过并记录） |
| 模型拒答/截断（单次 ≤16384 token） | 分块处理，已完成部分先行落盘，续写只改差异 |
| 题面歧义 | 记录歧义判据与双方依据，写入 `uncertainty`/`pending_human`，不擅自选一 |
| 双评分歧/参考答案缺失 | 记录分歧判据与双方依据，不擅自改分 |
| 材料冲突 | 同一指标两个来源不一致时两个都记录并标 `source_conflict`，不隐藏 |

## 4. 最小调用示例（示意，非正式案例结果）

```bash
# 1) 前三分钟先落盘骨架 + 来源快照
python scripts/main.py scaffold --input /input --output /app/output --work /app/work

# 2) Agent 精读材料，逐条登记事实台账到 /app/work/fact_source_map.json
# 3) Agent 写七要素章节稿到 /app/output/proposal_sections.md
#    并把"目标/内容/路线/指标"四组列表写入 /app/work/correspondence.json

# 4) 合并、校验、写 result.json
python scripts/main.py run --input /input --output /app/output --work /app/work

# 5) 交付前自检
python scripts/main.py selftest
python scripts/main.py verify --output /app/output
```

示意输出（标明为示例，不冒充正式案例结果）：

```json
{"ok": true, "case_id": "example_case", "status": "partial",
 "sources": 3, "fact_claims": 12, "selfcheck_issues": 0}
```

## 5. 边界与失效条件（创新证据）

- **可靠**：材料是单篇论文或成体系的申请材料（含前期成果、文献、数据），可稳定产出七要素稿 + 三级对应。
- **部分可靠**：材料只有摘要或碎片化笔记，七要素中"研究基础""技术路线"会缺证据，需降级为 `partial` 并写入 `material_gaps`。
- **失效**：材料是纯政策文本或与申请无关的综述，无法提炼申请人前期观察与"为什么是我们"，此时应输出 `insufficient_evidence` 并列出缺口，而非套模板填充。
- **跨学科**：本 Skill 不内置学科知识库，术语与文献以材料为准；跨度过大时逐条标 `unmapped`，不补造术语定义。
