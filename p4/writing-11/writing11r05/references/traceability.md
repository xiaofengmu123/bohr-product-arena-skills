# 溯源与输出契约规范

本文档是 `SKILL.md` 的补充规范，定义两件事：(1) `result.json` 每个字段的确切
含义与必填子字段；(2) 证据溯源的最小标准（locator + quote）。脚本
`scripts/main.py` 与 `scripts/core_impl.py` 按此规范实现和校验，Agent 写盘时
逐条对齐，避免「字段名漂移」。

## 1. result.json 字段规格

### 顶层必填字段

| 字段 | 类型 | 含义 |
|-|-|-|
| `case_id` | string | 来自 `/input/case.json` 的 `case_id`/`id`/`caseId`，原样保留 |
| `status` | string | `success \| partial \| failed \| insufficient_evidence \| input_error \| in_progress` |
| `deliverables` | array | 主产物；每项 `{path, format, title, description}` |
| `fact_source_map` | array | 事实与来源映射，见下 |
| `version_boundary` | object | 版本与边界，见下 |
| `sources` | array | 实际读过并使用的输入材料 `{path, type, role}` |
| `uncertainty` | array | 存疑项 `{item, reason, resolution}` |
| `pending_human` | array | 需人工确认项 `{item, reason}` |
| `change_log` | array | 每次落盘/修改 `{time, action, key, detail}`，由脚本自动追加 |

### 可选但建议填充

| 字段 | 类型 | 含义 |
|-|-|-|
| `limitations` | array | 缺数据/缺软件/降级项的显式声明 `{item, reason, fallback}` |
| `runtime` | object | `{started_at, ended_at}`，脚本自动盖时间戳 |

### fact_source_map 条目（每条可回溯）

```json
{"item": "结合亲和力", "kind": "number",
 "value": "3.5 million", "source": "paper.pdf#p3",
 "locator": "paper.pdf#p3, para 2", "quote": "3.5 million nM",
 "note": "原文逐字；单位 nM 原样保留"}
```

- `kind` 取值：`number | term | symbol | citation | figure | claim`。
- `source`：原文件路径 + 页码/版本记录；`locator`：可跳到原文的精确定位
  （段落/行/节）；`quote`：从材料中**逐字**取出的片段，禁止转写或凭记忆补写。
- 两者至少其一（推荐都写）；无逐字来源时 `quote` 写 `null` 并在 `note` 标注
  「无逐字来源，待核」。

### version_boundary 条目

```json
{"modifications": [{"what": "锁定术语", "why": "glossary", "before_after": {"before": "亲和力", "after": "结合亲和力"}}],
 "human_confirm": [{"item": "pH 单位", "reason": "原文未标注"}],
 "material_gaps": [{"item": "参考文献表", "reason": "PDF 未提供"}],
 "anomalies": [{"item": "超长段落", "reason": "超过 2MB 截断"}],
 "editable": true}
```

- `modifications` 每条必含 `what` / `why` / `before_after`，只记录真实改动，
  不改写作者原意，局部修改不扩大波及范围。
- `human_confirm` 与顶层 `pending_human` 保持一致（`finalize` 自动同步）。
- `editable=true` 表示工件是可编辑文本/Office，而非只读截图。

## 2. 证据溯源最小标准

1. **每个关键结论、数字、术语、引用、图表编号都要能指回材料**：给出
   `locator`（段落/行/节定位）与 `quote`（逐字引用）。
2. **PDF 必须真实解析正文**：原始输入只有 PDF，没有预提取答案层；不得凭文件名
   或领域标签臆测内容，不得补造数据。
3. **冲突信息不隐藏**：同一指标两个来源不一致时，两者都记录并标
   `source_conflict`，交 `pending_human`，不擅自取舍。
4. **不虚构引用、DOI、数值、渲染产物**：缺失就写 `pending_human` / `not_reported`
   / `material_gaps`，不要用示例或记忆填充。

## 3. 翻译四锁与检查器对应关系

| 锁 | 检查器函数（core_impl.py） | 失败表现 |
|-|-|-|
| 术语锁 | `check_glossary_terms` | 多 variant 混用 → warning |
| 数字锁 | `fact_number_fidelity`（权威）+ `heuristic_number_diff`（兜底） | 关键数字缺失 → error；疑似补写 → warning |
| 引用锁 | `check_citation_fidelity` | 源引用在译文中找不到 → warning |
| 结构锁 | `check_alignment` | 段空/无 locator/编号不连续 → error/warning |

`fact_number_fidelity` 是权威路径：关键数字先由 Agent 记入 `fact_source_map`，
再比对译文；`heuristic_number_diff` 是全篇粗扫，只出 warning，不作唯一依据。
数值归一化支持 `3,500,000` / `3 500 000` / `350万` / `3.5 million` /
`3.5 billion` 的等价判定；区间、科学计数法、带符号容差**保守跳过**并交人工，
绝不假装已解析。

## 4. 降级与诚实标注

- 材料缺失/无法判断：登记 `uncertainty`、`version_boundary.material_gaps`、
  `limitations`，`status=partial`，写「材料缺失，无法判断」。
- 缺软件（如无 Office 渲染器）：交付可编辑文本，并在 `sources`/`limitations`
  注明「未渲染预览，原因：缺少 X」，不谎称渲染成功。
- 超长截断：分段处理并记录段落边界；编号/交叉引用在分段后重新校验。
- 运行失败：保留已落盘骨架，`status=failed`，`anomalies` 记失败原因与时间；
  同一策略失败后换策略重试不超过两次。
- **红线**：不编造评测分数、运行结果、文献、数据或渲染产物；不确定就写
  「未确认」并保留来源证据。
