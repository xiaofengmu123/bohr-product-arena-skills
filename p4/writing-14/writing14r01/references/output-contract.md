# 输出契约（output-contract）

本文件定义 `result.json` 的字段语义、七要素结构、证据溯源 schema 与词表。它是 `scripts/main.py` 校验与 `SKILL.md` 执行流程的事实依据；`result.json` 与 `proposal.md` 的每一处结构化字段都以此为准。

## 1. 顶层字段

| 字段 | 类型 | 必需 | 说明 |
|-|-|-|-|
| `case_id` | string | 是 | 与 case.json 的 `case_id`/`id` 一致，保留输入标识 |
| `title` | string\|null | 契约字段 | 开题方案标题；缺材料时为 null 并记 uncertainty |
| `summary` | string\|null | 契约字段 | 摘要（建议 ≤300 字）；材料不足时在文末说明 limitations |
| `deliverable` | string | 是 | 主产物（可编辑稿）绝对路径，render 后指向 `proposal.md` |
| `seven_elements` | object | 契约字段 | 七要素对象，见 §2 |
| `evidence_map` | array | 契约字段 | 全部论断的证据扁平索引，见 §3 |
| `artifacts` | object | 契约字段 | 可编辑产物路径映射，见 §4 |
| `status` | string | 是 | `complete` / `partial` / `failed`，见 §5 |
| `sources` | array | 是 | 证据源清单，见 §6 |
| `uncertainty` | array | 是 | 不确定/待确认项，见 §7 |
| `pending_human` | array | 是 | 必须人工裁决/确认项，见 §7 |
| `change_log` | array | 是 | 修改记录，见 §8 |
| `runtime` | object | 是 | `started`/`finished`/`seconds`，记录运行时长 |
| `errors` | array | 是 | 运行错误（坏 JSON、坏编码、缺文件等） |
| `degradations` | array | 是 | 降级动作（缺软件、格式降级等） |
| `limitations` | array | 是 | 结果边界说明，随 `summary` 一起呈现 |

契约字段来源：case.json 的 `output_contract`（同义键 `output_schema`/`required_fields`/`contract`）；未声明时使用默认字段 `title`、`summary`、`deliverable`、`seven_elements`、`evidence_map`、`artifacts`。**原样回填全部契约键，未知键不删除、不更名**；缺值给 `null`/`[]`/`{}`。

## 2. seven_elements（七要素）

七个键名固定，值均为数组，数组元素为「论断对象」：

```text
background          研究背景与缺口（已知—未知—待验证）
problem_definition  研究问题定义（具体、可证伪）
objectives          研究目标与判断标准（指标、样本、对照、时间点）
technical_route     技术路线与验证方式（输入、输出、前后依赖）
stage_plan          阶段计划与里程碑（周期、经费、数据、人员相容）
risks               风险与备选方案（触发信号、影响、备选、决策时点）
expected_outcomes   预期成果与考核方式（可考核、边界写明）
```

论断对象：

| 键 | 类型 | 说明 |
|-|-|-|
| `claim_id` | string | 唯一标识，建议按要素前缀命名（`bg-1`、`obj-2`、`risk-3`） |
| `text` | string | 论断正文，忠于材料，不新增材料外事实 |
| `locator` | string | 证据定位：页码/段落/行号，如 `materials/paper.md#L12-L15` |
| `quote` | string | **逐字**引用的材料原文 |
| `source_id` | string | 指向 `sources` 清单中的源标识 |
| `kind` | string | `fact` / `summary` / `inference`，见 §9 |
| `verdict` | string | `supported` / `conflict` / `missing` / `not_reported`，见 §9 |

`stage_plan` 的论断对象额外支持 `months`（或 `duration`）字段，供可行性校验「排得开」：`scripts/main.py check` 汇总各里程碑时长并与 case.json 的给定周期比较。

## 3. evidence_map（证据溯源，本版重点）

`evidence_map` 是七要素论断的扁平索引，**每条关键结论都带 locator + 逐字 quote**，字段比普通 source 更细：

| 键 | 说明 |
|-|-|
| `claim_id` | 与七要素论断一一对应 |
| `section` | 所属要素（background 等） |
| `text` | 论断正文 |
| `locator` | 页码/段落定位 |
| `quote` | 逐字引用原文 |
| `source_id` | 证据源标识 |
| `kind` | fact/summary/inference |
| `verdict` | supported/conflict/missing/not_reported |

要求：`fact` 与 `summary` 类论断必须有非空 `locator` 且 `quote` 为材料原文逐字片段；`inference` 类允许无 quote，但必须写入 `uncertainty` 说明推断依据。评分人可据此复跑：给定 `locator` 打开材料即可找到对应 `quote`。

## 4. artifacts

对象，键为产物名、值为真实存在的绝对路径。至少包含：

- `proposal_md`：可编辑 markdown 稿（七要素分节、保留编号与证据标注）
- `evidence_map_json`：机器可读的证据映射

题目要求 docx/pptx/xmind 时，用运行时 `office-renderer`/`browser-automation` 生成，并附真实渲染的 PNG/PDF 预览路径；预览文件须真实存在，不可仅登记路径。

## 5. status

| 值 | 含义 |
|-|-|
| `complete` | 七要素齐全、契约字段齐、证据可回溯、FAIL=0 |
| `partial` | 有产出但存在材料缺口/未确认项/部分要素空 |
| `failed` | 输入错误或运行失败导致无有效结果 |

## 6. sources（证据源清单）

| 键 | 说明 |
|-|-|
| `source_id` | 唯一标识（S1、S2…） |
| `path` | 材料文件路径 |
| `locator` | 页码/段落定位 |
| `quote` | 关键原文逐字片段（可选但建议） |

## 7. uncertainty 与 pending_human

- `uncertainty` 元素：`item`、`issue`、`kind`（`missing`/`conflict`/`inference`/`out_of_scope`/`input_error`）、`action`。
- `pending_human` 元素：`item`、`issue`、`action`、`decision_point`。信息自相矛盾、指标不可达、周期过短、学科规范冲突、引用无法获取时**必须**写入本字段，不擅自裁决。

## 8. change_log

元素：`at`（ISO8601 时间）、`action`、`reason`。必须能还原「骨架 → 成品」的增量过程，尤其记录前 3 分钟落盘与后续改进。

## 9. 词表

`kind`：

| 值 | 含义 |
|-|-|
| `fact` | 原文可指认的事实/数字/引用 |
| `summary` | 多段材料归纳 |
| `inference` | Agent 补出的推断，须记 uncertainty |

`verdict`：

| 值 | 含义 |
|-|-|
| `supported` | 材料直接支持 |
| `conflict` | 材料间矛盾，两版本并列、不擅自选择 |
| `missing` | 材料缺失，不补造 |
| `not_reported` | 材料未报告，写入 pending_human |

风险分类（`risks` 要素按此分类）：

```text
data_access         数据获取
method_feasibility  方法可行性
schedule            进度
ethics_compliance   伦理与合规
```

## 10. 最小合法骨架示例

以下为**示意**（标注，非正式案例结果）：

```json
{
  "case_id": "demo",
  "title": null,
  "summary": null,
  "deliverable": null,
  "seven_elements": {"background": [], "problem_definition": [], "objectives": [],
    "technical_route": [], "stage_plan": [], "risks": [], "expected_outcomes": []},
  "evidence_map": [],
  "artifacts": {},
  "status": "partial",
  "sources": [],
  "uncertainty": [{"item": "seven_elements", "issue": "示例：未分析材料", "kind": "missing", "action": "回填"}],
  "pending_human": [],
  "change_log": [{"at": "2026-01-01T00:00:00Z", "action": "init skeleton", "reason": "示例"}],
  "runtime": {"started": "2026-01-01T00:00:00Z", "finished": null, "seconds": null},
  "errors": [],
  "degradations": [],
  "limitations": []
}
```

## 11. 附录：七要素撰写模板（可复用）

该模板是他人或别的 Agent 复用本 Skill 时的**计划模板**；方括号内为须从材料回填的内容，`[证据]` 处一律挂 `locator` + 逐字 `quote`。模板本身不含正式答案。

### 11.1 研究背景与缺口（background）

写法要求：三段式「已知—未知—待验证」，不写文献罗列。

```markdown
- [bg-1] 已知：[材料中的既有结论/约束，含数字须带 quote]
- [bg-2] 未知：[该领域尚未回答的具体问题]
- [bg-3] 待验证：[本课题要检验的假设]，指向 [bg-2] 的缺口
```

### 11.2 研究问题定义（problem_definition）

写法要求：一到三个可回答、可证伪的问题；每个问题对应一个目标。

```markdown
- [pq-1] 研究问题：[具体、可证伪的表述]，可判定条件：[是/否判断标准]
```

### 11.3 研究目标与判断标准（objectives）

写法要求：与问题一一对应，写清指标、样本、对照、时间点。

```markdown
- [obj-1] 目标：[对应 pq-1]，指标：[可考核指标]，样本：[样本量]，对照：[对照设置]，时间点：[判定时点]
```

### 11.4 技术路线与验证方式（technical_route）

写法要求：拆成有输入、输出、前后依赖的步骤，验证方式与目标对应。

```markdown
- [route-1] 步骤：[输入] -> [处理/方法] -> [输出]，依赖：[前置步骤]，验证：[对应 obj-1 的验证方式]
```

### 11.5 阶段计划与里程碑（stage_plan）

写法要求：落在给定周期/经费/数据/人员内；每项带 `months` 供可行性校验。

```markdown
- [sp-1] 里程碑：[任务内容]，months：[耗时]，资源：[人员/经费/数据]，关键路径：[是/否]，可并行：[是/否]
```

### 11.6 风险与备选方案（risks）

写法要求：按数据获取/方法可行性/进度/伦理与合规分类，每条写触发信号、影响、备选、决策时点。

```markdown
- [risk-1] 类别：[data_access|method_feasibility|schedule|ethics_compliance]，
  触发信号：[可观测信号]，影响：[影响范围]，备选：[方案]，决策时点：[何时启动备选]
```

### 11.7 预期成果与考核方式（expected_outcomes）

写法要求：可考核、边界写明。

```markdown
- [out-1] 预期成果：[成果形态]，考核方式：[如何验证达成]，边界：[不包含什么]
```

