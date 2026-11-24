# 自检清单、result.json 字段说明与降级策略

## 1. result.json 字段说明

运行期最终必须交付 `/app/output/result.json`，字段如下（`*` 为题目
output_contract 必含字段）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 输入案例标识（取自 case.json 的 `case_id`/`id`/`challenge_id`，不得虚构） |
| `status` | string | `in_progress` / `done` / `partial` / `failed` / `insufficient_evidence` / `input_error` |
| `artifact`* | object | `{path,title,format,preview}`；`path` 指向 /app/output 下可编辑文件 |
| `fact_source_map`* | object | 证据映射（见 rules.md 第 2 节），含 `claims/trace/conflicts/unmapped` |
| `version_boundary`* | object | `{modification_diff,manual_confirm,material_gaps,anomalies,limitations}` |
| `sources` | array | 真实用到的来源文件清单（`path/size/sha256/type`） |
| `uncertainty` | array | 不确定项、推断项、缺数据项，逐条说明原因 |
| `pending_human` | array | 需人工确认项 |
| `change_log` | array | 逐次修改记录 `{time_unix,action,reason,impact}` |
| `runtime` | object | 引擎名、版本、创建时间、预算常量 |
| `contract_hint` | object | case.json 显式 output_contract 的原样留存 |

**合同回填**：若 case.json 给出 `output_contract`/`output_schema`/
`required_fields`，这些键必须**全部并入 result.json**（缺值给 `null`/`[]`），
不改名、不删未知键。题目默认三字段 `artifact`、`fact_source_map`、
`version_boundary` 必须始终存在。

## 2. 逐条自检清单（对应评分维度，交付前逐条勾选）

### 实际效果（40 分）
- [ ] 任务完成度：按 task.md 指定体裁/受众/格式产出**可编辑章节稿＋七要素对照**，`artifact.path` 指向真实文件；不是提纲或模板套用。
- [ ] 结果正确性：每条数字/术语/引用/图表/政策/前期成果论断都有 `locator+quote`；`unmapped`/`conflict`/`pending_human` 单独说明。
- [ ] 结果完整性：七要素（立项依据、科学问题、研究目标、创新点、技术路线、研究基础与可行性、考核指标）齐备、厚度匹配、要素间衔接。
- [ ] 结果可核验性：`sources` 含 SHA256，`change_log` 完整，`trace` 三键闭环（段落→要素→结论）。

### 使用增益（25 分）
- [ ] 质量增益：判据可观察可操作（如"每个创新点点名具体已有工作"），而非"准确/完整"泛化。
- [ ] 效率增益：一次台账＋自检减少返工，`change_log` 体现增量改进而非推倒重写。
- [ ] 增益可验证性：结论基于材料证据与来源映射，不靠精选案例或自夸。

### 创新增益（15 分）
- [ ] 新增能力：把"立得住/说得准/对得上/查得到"译为可观察判据（rules.md 第 4 节）。
- [ ] 方法改进：结论级 locator+逐字 quote 的证据台账＋三级 trace 相对通用 rubric 有实质区分。
- [ ] 创新证据：`uncertainty`/`limitations` 写清边界与失效条件（哪类材料可靠、哪类失效）。

### 可靠体验（10 分）
- [ ] 调用易用：新评分员按 SKILL.md 可独立跑通；脚本路径相对、仅标准库。
- [ ] 运行稳定：重复运行结果一致（确定性排序、固定输出）。
- [ ] 异常处理：题面歧义、答案缺失、双评分歧、材料超长/乱码、模型拒答均有记录。

### 生态贡献（10 分）
- [ ] 文档完整：SKILL.md + rules.md + checklist.md 齐全，字段/版本/限制信息完整。
- [ ] 资源可复用：台账、快照、自检脚本可被他人直接用于自己的申请。
- [ ] 分享价值：沉淀建题/评分/复现方法与失败分析，而非一次性答案。

## 3. 自检命令

```bash
python scripts/main.py                          # 落盘骨架 + 快照，退出码 0
python scripts/evidence.py verify               # mapped 结论必须有 locator+quote
python scripts/evidence.py export --output /app/output   # 导出 evidence_map.json
python scripts/selftest.py --result /app/output/result.json --output /app/output
python scripts/selftest.py --demo               # 空输入鲁棒性自测
```

`selftest.py` 退出码 0 才允许把 `status` 设为 `done`；有 issue 时必须
`status=partial` 并列出缺口。

## 4. 降级策略（必须遵守）

- 缺数据/缺材料 → `version_boundary.material_gaps` + `uncertainty`，`status=partial`，绝不补造。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ `uncertainty` 写"未能生成渲染预览"，保留可编辑 Markdown/纯文本，不伪造预览。
- 材料超长/乱码 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不得声称已全部核对。
- 模型拒答/截断（单次 ≤16384 token）→ 分块处理，已完成块先落盘，续写只改差异。
- 双评分歧超一档、参考答案缺失 → 记录分歧判据与双方依据，不擅自改分。
