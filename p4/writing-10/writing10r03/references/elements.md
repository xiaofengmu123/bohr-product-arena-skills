# 字段定义与规则（审稿回复与论文修改）

本文档是 `result.json` 各字段的权威定义，供 Agent 写作与 `selftest.py` 校验共同遵循。

## 1. 输出契约三字段（题目 output_contract，不可改名/删键）

### 1.1 deliverable（目标产物）

```json
{
  "path": "revised-manuscript.md",
  "format": "markdown",
  "title": "修改稿标题（可回填为基线标题）",
  "sections": ["Introduction", "Methods", "...（实际节名）"],
  "status": "in_progress | complete | partial",
  "previews": []
}
```

- `path` 相对 `/app/output`；回复信与修改台账分别写 `response-letter.md`、`revision-log.md`（可编辑源文件）。
- docx/PPT/图表若产出，另渲染真实 PDF/PNG 预览并列入 `previews`，不得只有二进制不可见产物。

### 1.2 fact_source_mapping（事实与来源映射）

数组项：

```json
{
  "claim": "关键论断（一条一句）",
  "source_ref": "materials/manuscript-baseline.md",
  "location": "节/段落/锚点",
  "quote": "逐字原文片段",
  "status": "traced | assumption | unverified | source_conflict"
}
```

- 每条数字、术语、引用、图表、关键论断至少一项；`traced` 必须带逐字 `quote`。
- 冲突不隐藏：同一指标两来源不一致时，两条都记录并标 `source_conflict`。
- 不虚构 DOI/引用/数值；缺失写 `unverified` 或 `assumption`。

### 1.3 version_boundary（版本与边界）

```json
{
  "changes": [{"comment_id": "C1", "action": "具体动作", "locator": "Methods 段", "status": "done"}],
  "pending_confirmation": ["需人工确认项..."],
  "material_gaps": ["缺材料项..."],
  "anomalies": ["版本不匹配/意见有误/权限不足等异常"]
}
```

- `changes` 与 `response_ledger` 的 comment_id 一一对应；无孤儿 change。
- `pending_confirmation` 是顶层 `pending_human` 的明细来源。

## 2. 意见台账（response_ledger，本 Skill 核心结构化产物）

每项字段：

| 字段 | 取值 | 含义 |
|---|---|---|
| comment_id | "C1"、"C2"… | 稳定编号，与源意见一一对应，多轮返修沿用 |
| sub_requirements | ["子要求1", "子要求2"] | 一条意见内的多个子要求，每个都要有结论 |
| type | method/data/writing/format/ethics/citation/other | 意见类型 |
| severity | critical/major/minor/cosmetic | 严重程度 |
| disposition | accept/partial/reject | 采纳/部分采纳/不采纳 |
| tone | accept/clarify/rebut/appeal | 语气强度（见 §3） |
| action | "已修改→具体动作" | 落到正文的具体动作，拒绝空话 |
| locator | {section, paragraph, anchor_old, anchor_new} | 定位（见 §4） |
| evidence | {type, source, status, reason} | 证据（见 §5） |
| status | pending/done/not_needed/blocked | 处置到位状态 |

## 3. 语气四档

- **accept（接受）**：同意 + 具体动作 + 定位 + 证据；不做笼统致谢。
- **clarify（澄清）**：复述关切、澄清误解、补上下文；不反驳。
- **rebut（礼貌反驳）**：有证据地不同意，尊重对方，不冒犯、不轻蔑。
- **appeal（申诉）**：对事实性错误提出正式异议，只用于有强证据时，写给编辑。

禁止：冒犯性表达、过度承诺（"全面解决"、"彻底验证"）、无原则顺从（对错误意见也照单全收）。

## 4. 定位格式（locator）

```json
{
  "section": "Methods",
  "paragraph": 3,
  "anchor_old": "N=120 samples",
  "anchor_new": "N=140 samples"
}
```

- 用「原文锚点 → 改后锚点」而非页码行号，改稿后仍可检索。
- `anchor_new` 必须是修改稿中真实存在的连续字符串；`anchor_old` 应能在基线中找到。
- 改稿后若锚点被拆散，必须同步更新 locator（`selftest.py` 会检索失败并报警）。

## 5. 证据清单（evidence）

```json
{
  "type": "data | literature | analysis | experiment | other",
  "source": "出处（材料内路径或可复核定位）",
  "status": "到位 | in_progress | 无法补做 | not_needed",
  "reason": "无法补做时的替代说明"
}
```

- 补实验/补数据/补文献/重做分析类意见必须给出可查证证据；无法补做时说明原因与替代方案，不硬填。
- 本演练不冒称真实补做实验；证据来自组织方基线材料或明确标注为方案建议（assumption）。

## 6. 确定性与复现约定

- 脚本确定性：`main.py` 骨架、`selftest.py` 校验不依赖墙钟时间；同输入两次运行 canonical 骨架一致。
- 内容指纹：`runtime.content_hash` 是对除 `runtime` 外全部字段的 sha256 前 16 位；Agent 每次修改后必须重算。
- canonical 比对时剔除的易变字段：`runtime`、`change_log`、以及 `ts/timestamp/elapsed` 等时间键。
- LLM 正文不保证逐字相同，但**覆盖与要素（comment_id、台账条目数、约束）保证不漂移**；漂移由 `selftest.py` 检出。

## 7. 状态词汇

`status`：`in_progress`（进行中）/`complete`（完整）/`partial`（部分，带 limits）/`failed`（失败）/`insufficient_evidence`（材料不足）/`input_error`（输入错误）。

`fact_source_mapping.status`：`traced`/`assumption`/`unverified`/`source_conflict`。

## 8. 边界

- 只做审稿回复子场景，不做整篇重写、通用综述或跨学科泛化。
- 没有真实材料时输出 `insufficient_evidence` 并列出缺口，不用示例填充。
- 交付前在非作者环境重跑 `python scripts/selftest.py --demo` 与真实输入自检。
