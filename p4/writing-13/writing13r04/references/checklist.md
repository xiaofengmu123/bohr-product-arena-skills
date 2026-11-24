# 自检清单、降级策略与 result.json 字段说明

## 1. 逐条自检清单（对应评分维度）

执行到 Step 4/5 时逐条勾选；未勾选即视为未通过，不得把 status 写成 done。

### 实际效果（40 分）
- [ ] 任务完成度(15)：已按 task.md 指定体裁/受众/格式产出七要素“章节稿”，`artifact.path` 指向真实存在的可编辑文件，而非提纲/建议/整篇模板套用。
- [ ] 结果正确性(15)：每个数字/术语/引用/图表/论断都有 `source_ref` 或标 `not_reported`；`verified/paraphrased` 必有 locator+quote；`inferred/conflict` 单独说明；`done` 与 `proposed` 不混写；申请人前期证据缺失时不补造。
- [ ] 结果完整性(5)：七要素齐全、厚度匹配、要素间衔接；目标↔内容↔路线↔考核指标逐条对应，分歧已记入 `anomalies`。
- [ ] 结果可核验性(5)：`sources` 列全来源、`change_log` 完整、来源清单含 SHA256；段落—要素—证据三级对应可回溯。

### 使用增益（25 分）
- [ ] 质量增益(15)：判据可观察可操作（“该句有 locator+quote 才可下断言”“not_reported 必须显式占位”），而非泛泛“准确、完整”。
- [ ] 效率增益(5)：一次通过台账＋骨架＋自检减少返工轮次，`change_log` 体现增量改进而非推倒重写。
- [ ] 增益可验证性(5)：结论基于材料证据与来源映射，不靠精选案例或自我夸奖。

### 创新增益（15 分）
- [ ] 新增能力(5)：把“立得住/说得准/对得上/查得到”译为可观察判据，而非只给总分或范文。
- [ ] 方法改进(5)：证据四分类、done/proposed 二分类、目标-路线-指标一致性回检、诚实降级等相对通用 rubric 有实质区分。
- [ ] 创新证据(5)：`limitations`/`uncertainty` 写清边界，说明做法在哪类材料可靠、哪类失效。

### 可靠体验（10 分）
- [ ] 调用与易用性(3)：SKILL.md 与 references 足以让一名新评分员独立跑通。
- [ ] 运行稳定性(4)：脚本仅用标准库、路径相对化、重复运行结果一致；`main.py --help` 退出码 0。
- [ ] 异常处理(3)：题面歧义、材料缺失、引用无法核实、学科跨度过大、超长/乱码、格式冲突均有记录与处置。

### 生态贡献（10 分）
- [ ] 文档完整(3)：SKILL.md + rules.md + checklist.md 齐全，字段与版本/限制信息完整。
- [ ] 资源可复用(4)：台账、快照、自检脚本、七要素表可被他人直接用于自己的申请。
- [ ] 分享价值(3)：沉淀了建稿/溯源/一致性回检方法与失败分析，而非一次性答案。

## 2. result.json 字段说明

- `case_id`：来自 case.json，不得虚构。
- `status`：`in_progress`/`done`/`partial`/`failed`/`insufficient_evidence`。时间不足或材料缺失一律 `partial`，材料全无 `insufficient_evidence`，禁止把未完成写成 `done`。
- `artifact`：目标产物描述。`path` 指向可编辑文件；Office/slide/diagram 需另附真实渲染的 PDF/PNG 预览并注明。
- `fact_source_map`：事实与来源映射数组，条目含 `id/element/claim/ev_status/tense/locator/quote/note`，见 `rules.md`。
- `seven_elements`：`covered`（七要素是否覆盖）＋`consistency`（目标/内容/路线/指标数量与 `aligned`）。
- `version_boundary`：`modification_diff`（修改差异）、`manual_confirm`（需人工确认项）、`material_gaps`（材料缺口）、`anomalies`（异常）。
- `sources`：真实使用到的来源文件清单（路径、大小、SHA256、类型）。
- `uncertainty`：不确定项、推断项、缺数据项，逐条说明原因。
- `limitations`：本 Skill 在该案例下的失效条件与边界（须写清，不空泛）。
- `pending_human`：需申请人/人工补充或确认的项。
- `change_log`：逐次修改记录（时间/动作/原因/影响）。

## 3. 降级策略（必须遵守）

- 缺数据/缺材料 → `version_boundary.material_gaps`＋`limitations`＋`uncertainty`，`status=partial`，正文写“【材料未提供，待申请人补充】”；绝不补造缺失内容。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ `uncertainty` 写明“未能生成渲染预览”，用纯文本/Markdown 保留可编辑中间产物，不伪造预览。
- 材料超长或格式错乱 → 分段读取并记录已读范围；未读部分写入 `material_gaps`，不声称已全部核对。
- 模型拒答/输出被截断（单次 ≤16384 token）→ 分块处理，已完成的中间结果先落盘，续写只改差异部分。
- 冲突/双评分歧 → 记录双方依据，标 `source_conflict`，不擅自改分。
- 空输入目录 → 退出码 0，写合法 `insufficient_evidence` 结果，不抛异常。

## 4. 最小调用示例（示意，非正式案例）

```bash
python3 scripts/main.py --case /input/case.json --out /app/output --work /app/work
python3 scripts/ledger.py add --element 创新点 --status paraphrased --tense done \
  --claim "该工作改进了X" --locator "materials/paper.md#p3" --quote "we improved X"
python3 scripts/ledger.py verify
python3 scripts/ledger.py export --out /app/work/evidence_map.json
python3 scripts/selftest.py --case /input/case.json --out /app/output
```

示意输出（标注为示例）：`{"ok": true, "issues": [], "status": "partial", ...}`。以上不冒充任何正式案例结果。
