# 自检清单、result.json 字段说明与降级策略

## 1. 逐条自检清单（对应评分维度，执行到 Step 4/5 逐条勾选）

### 实际效果（40 分）
- [ ] 任务完成度(15)：产出**可直接进入申请书的章节稿 + 要素对照说明**，体裁/受众/格式符合 task.md，`artifact.path` 指向真实文件。
- [ ] 结果正确性(15)：每个数字/术语/引用/图表/论断都有 `source_ref` + `locator` + 逐字 `quote`；`unmapped`/`conflict` 单独说明；"已完成/拟开展"分清。
- [ ] 结果完整性(5)：七要素齐全、厚度匹配、要素衔接；评测类任务覆盖"题面—参考答案—评分锚点—逐题评分—评测报告"闭环。
- [ ] 结果可核验性(5)：段落—要素—证据三级对应；`sources` 含 SHA256；`change_log` 完整。

### 使用增益（25 分）
- [ ] 质量增益(15)：判据可观察可操作（每句论断有 quote、目标数=内容数=指标数），而非泛泛"准确、完整"。
- [ ] 效率增益(5)：台账 + 自检减少返工，`change_log` 体现增量改进而非推倒重写。
- [ ] 增益可验证性(5)：结论基于材料证据与来源映射，不靠精选案例或自我夸奖。

### 创新增益（15 分）
- [ ] 新增能力(5)：把"立得住/说得准/对得上/查得到"翻译为可观察判据。
- [ ] 方法改进(5)：七要素一致性回检、来源快照、逐字 quote、诚实降级相对通用 rubric 有实质区分。
- [ ] 创新证据(5)：`uncertainty`/`limitations` 写清边界，说明在哪些材料下可靠、哪些会失效。

### 可靠体验（10 分）
- [ ] 调用与易用性(3)：SKILL.md + references 足以让新评分员独立跑通。
- [ ] 运行稳定性(4)：脚本仅标准库、路径相对、重复运行结果一致。
- [ ] 异常处理(3)：空输入/坏 JSON/坏编码/缺参数/超大文件/材料缺失/拒答均有识别与处置记录。

### 生态贡献（10 分）
- [ ] 文档完整(3)：SKILL.md + rules.md + checklist.md 齐全，字段与版本/限制信息完整。
- [ ] 资源可复用(4)：七要素框架、台账、快照、自检脚本可被他人直接用于自己的申请书。
- [ ] 分享价值(3)：沉淀写作方法与失败分析，而非一次性答案。

## 2. result.json 字段说明

- `case_id`：输入案例标识（来自 case.json，不得虚构；缺失时为 `null` 并进 `pending_human`）。
- `status`：`in_progress | done | partial | insufficient_evidence | input_error | failed`。时间不足或材料缺失一律 `partial`，禁止把未完成写成 `done`。
- `artifact`：目标产物描述，`path` 指向 /app/output 下的可编辑文件；Office/slide/diagram 需另附真实渲染 PDF/PNG 预览并在 `path` 或 `sources` 注明。
- `fact_source_map`：事实与来源映射数组，字段见 rules.md（claim/kind/source_ref/locator/quote/status）。
- `version_boundary`：`modification_diff`（修改差异）、`manual_confirm`（需人工确认项）、`material_gaps`（材料缺口）、`anomalies`（异常）。
- `sources`：真实使用到的来源文件清单（路径、定位、类型、SHA256）。
- `uncertainty`：不确定项、推断项、缺数据项，逐条说明原因。
- `pending_human`：需要人工确认/补齐的项（材料未区分时态、引用缺卷期页码等）。
- `change_log`：逐次修改记录（时间/动作/原因/影响）。
- `errors`：输入解析/读取错误（坏 JSON、坏编码、读失败等）。
- `degradations`：降级记录（超大文件跳过、预览未生成等）。
- `runtime`：`started_at` / `finished_at` / `seconds`。

## 3. 降级策略（必须遵守，鲁棒性为本版硬要求）

- 空输入目录 → 退出码 0，写合法 `insufficient_evidence` 结果并列出缺失项，不抛异常。
- 坏 JSON/坏编码 → 捕获并记录到 `errors`/`degradations`，继续处理其余材料，`status` 视情况为 `input_error` 或 `partial`。
- 缺参数/未知参数 → `--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`，本地回退 `input/` 等。
- 超大文件（>2MB）→ 跳过并记入 `degradations`，已读部分写入 `material_gaps`，不声称已全部核对。
- 写盘失败 → 降级到最小合法产物，不让进程崩溃，错误写到 stderr 并返回非零退出码。
- 缺材料/缺指南 → 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`，绝不补造。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ `uncertainty` 写明"未能生成渲染预览"，保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`。
- 模型拒答/截断 → 分块处理，已完成部分先行落盘，续写只改差异。
- 冲突与双评分歧 → 记录双方依据与判据，不擅自选一、不擅自改分。
- 重复运行 → 同一输入两次运行结果一致（确定性、固定 key 排序）。
