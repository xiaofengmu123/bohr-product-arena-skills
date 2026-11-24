# 自检清单、result.json 字段说明与降级策略

## 1. 逐条自检清单（对应评分维度，提交前逐条勾选）

### 实际效果（40 分）
- [ ] 任务完成度：已按 task.md 指定体裁 / 受众 / 格式产出**可直接进入申请书的七要素章节稿**（真段落正文，非建议 / 提纲 / 模板套用），`artifact.path` 指向真实文件。
- [ ] 结果正确性：每个数字 / 术语 / 引用 / 图表 / 论断都有 `source_ref + locator + quote`；`unmapped` / `conflict` 已单独说明；已完成与拟开展已正确区分。
- [ ] 结果完整性：七要素齐全、厚度匹配要求，`consistency_matrix` 覆盖目标↔内容↔路线↔指标，正文要素之间衔接。
- [ ] 结果可核验性：`sources` 列全来源文件并含 SHA256；`change_log` 完整；段落—要素—证据构成可复核三级对应。

### 使用增益（25 分）
- [ ] 质量增益：相较"读一遍直接写"，四问门 + 台账 + 一致性矩阵稳定提升要素齐备、论断有据、目标路线一致。
- [ ] 效率增益：先落盘骨架、台账增量登记、自检一次通过，减少返工轮次；`change_log` 体现增量而非推倒重写。
- [ ] 增益可验证性：结论基于材料证据与来源映射，不靠精选案例或自我夸奖。

### 创新增益（15 分）
- [ ] 新增能力：把"立得住 / 说得准 / 对得上 / 查得到"译成可观察判据，并产出七要素对照 + 一致性矩阵，而非只给总分或范文。
- [ ] 方法改进：已完成 / 拟开展 tense 标注、逐字 quote 溯源、诚实降级相对通用 rubric 有实质区分。
- [ ] 创新证据：`uncertainty` / `limitations` 写清边界，说明做法在哪类材料下可靠、哪类失效。

### 可靠体验（10 分）
- [ ] 调用与易用性：新评分员按 SKILL.md 可独立跑通。
- [ ] 运行稳定性：脚本仅标准库、路径相对化、重复运行结果可解释一致。
- [ ] 异常处理：题面歧义、指南不明、答案缺失、材料超长 / 乱码、引用无法核实、学科跨度过大、格式冲突均有识别与处置记录。

### 生态贡献（10 分）
- [ ] 文档完整：SKILL.md + rules.md + checklist.md 齐全，字段与版本 / 限制信息完整。
- [ ] 资源可复用：要素清单、四问门、一致性检查表、台账 / 快照 / 自检脚本可被他人用于自己的申请。
- [ ] 分享价值：沉淀写作方法与失败分析，而非一次性答案。

## 2. result.json 字段说明

- `case_id`：输入案例标识（来自 case.json，不得虚构）。
- `status`：`partial`（进行中 / 有缺口）/ `done`（完成）/ `failed`（失败）/ `insufficient_evidence`（材料不足）。时间不足或材料缺失一律 `partial`。
- `artifact`：目标产物描述。`path` 指向可编辑文件（md / tex / docx）；Office / slide / diagram 另附真实渲染 PDF/PNG 预览并在 `path` 或 `sources` 注明。
- `fact_source_map`：事实-来源映射数组，字段见 `rules.md` 第 3 节。
- `element_map`：七要素 → 是否覆盖 / 产物定位 / 关键论断。
- `consistency_matrix`：目标↔内容↔路线↔指标对应表及 `issues`。
- `sources`：真实使用到的来源文件清单（path / locator / type / sha256）。
- `uncertainty`：不确定项、推断项、缺数据项，逐条说明原因。
- `pending_human`：需人工确认项。
- `change_log`：逐次修改记录（ts / action / reason / impact）。
- `version_boundary`：`modification_diff`（修改差异）、`manual_confirm`（人工确认项）、`material_gaps`（材料缺口）、`anomalies`（异常）。
- `contract_hint`：题目 `output_contract / output_schema / required_fields` 原样保留，必须全部并入 result.json（最高优先级）。
- `selftest`：自检报告（passed + checks），由 `scripts/selftest.py` 生成。

## 3. 降级策略（必须遵守）

- 缺数据 / 缺材料 → `version_boundary.material_gaps` 与 `uncertainty` 如实标注，`status=partial`；严禁补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ 在 `uncertainty` 写明"未能生成渲染预览"，保留可编辑中间产物，不伪造预览。
- 材料超长 / 格式错乱 → 分段读取并记录已读范围；未读部分写入 `material_gaps`，不得声称已全部核对。
- 模型拒答 / 输出截断 → 分块处理，已完成部分先行落盘，续写只改差异。
- 双评分歧、参考答案缺失、指南不明 → 记录分歧判据与双方依据，不擅自改分、不擅自选一。
- 引用无法核实 → 标 `unmapped` 并进 `pending_human`，正文不得写成确证事实。

## 4. 阶段预算（总 900s / 64 请求 / 单响应 ≤16384 token）

| 阶段 | 时间 | 请求 | 输入 → 输出 | 强制动作 |
|---|---|---|---|---|
| Step 0 落盘骨架 | 0–180s | 1–3 | 读 case.json 头 + task.md 前部 → 写 partial result.json | **必须**：`python scripts/skeleton.py` 先落盘 |
| Step 1 盘点材料 | 180–360s | 3–6 | `ledger.py snapshot` → source_manifest.json | 列清单、标题、摘要，定体裁 / 受众 / 格式 / 语言 / 字数 / 引用风格 |
| Step 2 事实台账 | 360–600s | 8–15 | 分段精读 → `ledger.py add`（quote+locator+tense） | 数字 / 术语 / 引用 / 图表 / 论断逐条登记 |
| Step 3 起草产物 | 600–780s | 20–30 | 台账 → 七要素章节稿 + 要素对照 + 一致性矩阵 | 每完成一块即更新 result.json |
| Step 4 自检 | 780–870s | 5–8 | `ledger.py verify` + `export` + `selftest.py [--fix]` | 修复来源缺口与格式问题 |
| Step 5 收尾 | 870–900s | 3–6 | 终审 result.json | 按实设置 status，不足保留 partial |

时间不足时：优先保证 Step 0 与 Step 3 的中间产物已落盘；未完成部分写入 `version_boundary`，禁止把 `partial` 改成 `done`。
