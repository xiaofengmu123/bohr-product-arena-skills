# 输出契约对齐与 change_log 规范

本 Skill 的核心设计取舍是**输出契约完整**：`result.json` 不是"大致对得上"，而是
对 `output_contract` 做**逐字段镜像**，并用**逐条 change_log** 记录每一次写入。本文件
讲清字段如何对齐、change_log 如何维护、以及缺值时如何诚实标注。

## 1. result.json 的组成

`result.json = 契约镜像 + 信封`，两层合并，契约字段原样保留、未知键不删。

### 1.1 契约镜像（contract mirror）

`scaffold` 从 `/input/case.json` 或 `task.md` 抽取 `output_contract`，按以下规则生成骨架：

| 契约值类型 | 骨架值 | 说明 |
|---|---|---|
| `dict` | 递归保留全部键，叶子置 `null` | 嵌套结构完整镜像 |
| `list` | `[]` | 空列表，逐条填入 |
| 标量 | `null` | 由模型填入真实值 |

原始契约（含示例值与 item schema）保存在 `/app/work/contract.json`，模型据此了解
`fact_source_map` 等每项应有的键。抽取不到契约时回退到默认三字段：
`deliverable` / `fact_source_map` / `version_boundary`。

### 1.2 信封（envelope）

| 字段 | 类型 | 语义 |
|---|---|---|
| `case_id` | string | 输入标识，非空 |
| `status` | string | `ok / partial / insufficient_evidence / input_error` |
| `sources` | list | 每条含 path/type/bytes/digest |
| `uncertainty` | list | 文体/受众不明、结论不确定等 |
| `change_log` | list | 逐条审计（见下） |
| `pending_human` | list | 需人工确认项 |
| `errors` | list | 校验错误 |
| `degradations` | list | 降级处理记录 |
| `runtime` | dict | 测量元数据（预算、阶段、时间） |

## 2. change_log 规范

`change_log` 是**只追加（append-only）**的审计列表，记录 result.json 每次被修改的
字段、前后值与理由。每项：

```json
{
  "time": "ISO-8601（测量元数据，不影响结论）",
  "stage": "scaffold | verify | fix | finalize | selfcheck",
  "field": "被改字段（JSON 路径风格）",
  "before": "改前摘要",
  "after": "改后摘要",
  "reason": "为什么改，回退还是写明依据"
}
```

规则：

1. 至少 `scaffold` 与 `finalize` 各记一条。
2. 契约字段从 `null` 被填入真实值时，必须记一条（field=该契约字段）。
3. 任何"回退原句"或"保留但标 `pending_human`"的处置，必须在 reason 写明。
4. 大值（全文）只存摘要（截断或 `list[N]`），避免日志膨胀。

## 3. 逐字段对齐的检查点

`selfcheck` 与 `scripts/checks.py::check_contract` 逐项验证：

- 契约的每个叶子键（含嵌套）在 result.json 中都存在；
- 信封九个字段齐全，`status` 合法，`case_id` 非空；
- `sources` / `uncertainty` / `pending_human` / `errors` / `degradations` 是列表，
  `runtime` 是对象；
- `change_log` 每项含 `stage` / `field` / `reason`；
- `deliverable` 指向的文件真实存在于 `/app/output/`。

## 4. 缺值与缺口（不编造）

- 契约字段无法从材料推导 → 保持 `null` 并在 `pending_human` 记一条，**不填示例值**。
- 材料冲突 → 两来源都记入 `sources`，标 `source_conflict`，不静默选边。
- 数字/引用缺失 → 写 `not_reported`；需要人工补 → `pending_human`。
- 缺渲染软件（Office/slide/diagram 无法出 PDF/PNG）→ 交付可编辑主产物，并在
  `degradations` 注明"未生成预览"，**不伪造 PDF**。

## 5. 示例（示例，非正式案例）

输入 `case.json` 含 `output_contract: {"deliverable": "manuscript.md",
"fact_source_map": [...], "version_boundary": [...], "custom_note": {"lang": "en"}}`。

`scaffold` 写出的 result.json 骨架：

```json
{
  "deliverable": null,
  "fact_source_map": [],
  "version_boundary": [],
  "custom_note": { "lang": null },
  "case_id": "case-1",
  "status": "partial",
  "sources": [],
  "uncertainty": [],
  "change_log": [ { "time": "...", "stage": "scaffold", "field": "result",
                     "before": null, "after": "partial", "reason": "..." } ],
  "pending_human": [], "errors": [], "degradations": [], "runtime": {}
}
```

`custom_note` 是题目契约里的未知键，镜像后保留、未被删除；模型填入 `custom_note.lang`
时在 `change_log` 记一条。
