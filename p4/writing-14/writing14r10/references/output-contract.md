# 输出契约与字段语义

本文档定义 `/app/output/result.json` 的字段、词表与证据结构。脚本 `scripts/main.py` 的
`validate` 子命令依据本文件实施校验；SKILL.md 为执行时总纲。

## 1. 顶层结构

`result.json` 必须同时包含两类字段：

1. **题目 `output_contract` 字段**：优先从 `/input/case.json` 的 `output_contract` /
   `output_schema` / `contract` / `required_fields` 等键抽取并原样回填；缺值给
   `null`/`[]`，不改名、不删未知键。契约未声明时，使用默认契约字段（见下）。
2. **信封字段**（与契约并列，始终存在）：`case_id`、`status`、`sources`、
   `uncertainty`、`pending_human`、`change_log`，另加 `errors`、`degradations`、
   `runtime`。

## 2. 默认契约字段（开题报告与研究计划书七要素）

| 字段 | 类型 | 语义 |
|---|---|---|
| `title` | string | 项目名称，与 case/task 一致 |
| `background` | object | 研究背景与缺口，含 `known`/`unknown`/`to_verify`/`sources` 四槽 |
| `research_questions` | list | 一到三个可证伪研究问题，每条含 `id`/`question`/`falsifiable`/`sources` |
| `objectives` | list | 目标与判断标准，每条含 `id`/`question_ref`/`goal`/`metric`/`target`/`judgment`/`sources` |
| `technical_route` | list | 技术路线，每条含 `id`/`step`/`input`/`output`/`depends_on`/`parallel_with`/`sources` |
| `schedule` | list | 阶段计划，每条含 `id`/`milestone`/`month`(或 `month_start`/`month_end`)/`tasks`/`sources` |
| `risks` | list | 风险，每条含 `id`/`category`/`risk`/`trigger`/`impact`/`mitigation`/`decision_point`/`sources` |
| `expected_outcomes` | list | 预期成果，每条含 `id`/`objective_ref`/`outcome`/`assessment`/`sources` |
| `citations` | list | 引用，每条含 `claim`/`citation`/`sources` |
| `artifacts` | list | 可编辑产物与预览，条目为 `{path, format}` 或裸字符串 |

可选辅助字段：`alignment`（对象，含 `matrix` 对齐矩阵）、`period`（对象，含
`start`/`end` 与预算/人员/数据条件）、`constraints`（对象，约束集合）、
`contract_fields`（记录实际回填的契约字段名，供 `validate` 复跑）。

## 3. 七要素与四个核心问题

- **立得住**：`background` 的 `unknown` 必须被 `research_questions` 覆盖；问题须有
  可证伪判据（`falsifiable`），而不是领域综述。
- **对得上**：`objectives.question_ref` -> `research_questions.id`；
  `expected_outcomes.objective_ref` -> `objectives.id`；`alignment.matrix` 逐行给出
  问题/目标/路线/成果的四元对应。
- **排得开**：`schedule` 的月份落在 `period.start`..`period.end` 内；里程碑与
  `period` 声明的预算、数据、人员条件相容（超出即 WARN）。
- **说得清**：`risks` 按 `category` 分类（见下），每条有触发信号、影响范围、备选
  方案与决策时点；关键论断挂 `sources` 并逐字引用。

## 4. 词表

**风险分类 `category`**（题目指定）：`data_acquisition`（数据获取）、
`method_feasibility`（方法可行性）、`progress`（进度）、`ethics_compliance`（伦理与合规）。

**证据类型 `kind`**：`fact`（原文可逐字指认）、`summary`（多段归纳）、
`inference`（Agent 补出，须列入 `uncertainty`）、`conflict`（材料互相矛盾，并列呈现）、
`missing`（材料缺失）。

**状态 `status`**：`complete`、`partial`、`failed`、`insufficient_evidence`、
`input_error`。材料不足用 `insufficient_evidence` 或 `partial`，不得用 `complete`
掩盖缺口。

## 5. source 结构

```json
{"id": "S1", "locator": "materials/paper.pdf#p3 或 materials/notes.md#段5",
 "quote": "逐字引用的原文片段", "kind": "fact"}
```

- `locator` 定位到材料文件 + 页码/段落；`quote` 为逐字片段（二者至少其一）。
- 节点/要素/关系的 `sources` 为 source id 数组，或含 `locator`+`quote` 的内联对象。
- 每个被引用的 `id` 必须能在顶层 `sources` 找到；`validate` 会检查悬空引用。

## 6. 可编辑产物

`scripts/main.py render` 从 `result.json` 生成（纯标准库、确定性）：

- `proposal.md`：七要素正文，保留章节编号 1–8，可继续编辑；
- `evidence_map.md`：source 清单与逐字引用，供逐条回溯；
- `alignment.md`：问题/目标/路线/成果对齐矩阵（Markdown 表格）。

题目要求 docx/pptx 时，用运行时 `office-renderer`/`browser-automation` 生成并渲染
真实 PNG/PDF 预览；工具不可用时降级为 Markdown 并记入 `degradations`，不编造。
