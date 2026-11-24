# 字段级契约与定位规范

## 1. result.json 顶层字段

| 字段 | 类型 | 语义 |
|---|---|---|
| `case_id` | string | 从输入原样带回，不允许改写 |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error` |
| `result` | object | 盘点摘要与分组、检索结果、操作记录 |
| `claims` | array | 逐条结论，每条含 claim_id/text/confidence/evidence |
| `sources` | array | 输入文件清单（source_id + locator + sha256） |
| `uncertainty` | object | `{summary, count, items}`，机械启发式与未定项 |
| `uncertainties` | array | 逐条不确定性 |
| `change_log` | array | 操作记录，含 before/after/reason/locator |
| `pending_human` | array | 需人工裁定的冲突/缺口 |
| `errors` | array | 读取/解析/遍历错误 |
| `degradations` | array | 降级记录（缺工具、缺文本层、超限、契约键未映射等） |
| `runtime` | object | 运行元信息；不参与确定性对比 |

此外，`output_contract` 的全部字段必须**原样逐键回填**到顶层：dict 形态取键名，list 形态取字符串或
`name/key/field/field_id/id` 之一；缺值回填 `null`/`[]`，不改名、不删未知键。无法映射到内部 section 的
契约键回填 `null`，并记入 `degradations`（`contract_key_unmapped`）。

## 2. 状态语义（诚实优先）

- `ok`：材料完整、模型已填且 check 校验通过、结论有证据。
- `partial`：已落盘可用但存在缺口（无模型、部分冲突未裁定、部分降级）。
- `insufficient_evidence`：无文件或材料不足以完成盘点。
- `input_error`：case.json 缺失或坏 JSON；仍尽量盘点其余材料。

任何中途异常都不允许留下空产物：`init` 前 3 分钟内先写一版 `partial` 骨架。

## 3. 定位规范（locator）

- 定位一律用**输入目录内的相对路径**（`rel_path`），不出绝对路径、不出 `/app/output` 内路径。
- 文件级定位：`materials/report.pdf`；行级可加 `#L12`；组级用 `stem:xxx` 或 `sha256:xxx`。
- 证据对象形如 `{"source_id": "F0001", "locator": "materials/report.pdf", "quote": "逐字片段"}`。
- `source_id` 必须是 library.json 中真实存在的文件 id（或特殊值 `case`，指向 case.json）。
- `quote` 必须能在源文件中**逐字命中**；二进制文件无文本层时，quote 取 `rel_path` 本身，并如实标注
  文本层缺失，不编造高亮/批注/OCR 内容。

## 4. 中间表示（可复核设计）

| 文件 | 位置 | 作用 |
|---|---|---|
| `library.json` | work/ | 确定性事实层：文件/目录/哈希/分组/索引，不含语义解释 |
| `plan.json` | work/ 与 output/ | 执行计划：输入画像、阶段、预算分配、契约键 |
| `model.json` | work/ 与 output/ | 语义中间表示：别名/版本裁定、关系、批注、检索答案、恢复操作 |
| `check_report.json` | work/ | model 结构与证据 quote 命中校验报告 |
| `result.json` | output/ | 最终交付（事实层 + 语义层合并收口） |

事实层与语义层分离，使"确定性盘点"与"模型判断"可分别复核；`check` 只拦编造与错源，
`build` 只做机械合并，不重写模型结论。

## 5. 确定性约定

- 所有 JSON 统一 `ensure_ascii=False`、`indent=2`、`sort_keys=True`，末尾换行。
- 排序统一按 `rel_path` / 字典序；无随机源、无时间戳（`runtime` 除外）。
- 同输入两次运行，除 `runtime` 外逐字节一致。
