# slides-01 输入输出契约（字段规范）

## 1. 运行时输入（只读事实依据）

- `/input/case.json`：`case_id` + `output_contract`（字段名以它为准，运行时才可知）。
- `/input/task.md`：任务说明、目标听众/时长/页数上限、模板要求。
- `/input/` 下其余材料：论文 PDF / 汇报稿 / 大纲 / 模板 pptx / 图。不假设固定文件名，
  用 `python3 -B scripts/main.py inspect --input /input` 先发现，再分类。

读取规则：`utf-8-sig` 优先，坏编码降级为 `errors="replace"`；单文件 >2MB 跳过并记录；
PDF 用平台 `pdf-text-tool`/`pdftotext` 抽取，实际 PDF 无预提取答案层，须读真实文件。

## 2. 运行时输出（写 /app/output/）

- `result.json`（必须，缺它 = 0 分）、`presentation.pptx`（可编辑源）、`presentation.pdf`
  （页数必须与 pptx 一致，作为渲染预览）、`report.md`（方法说明与限制）、可选 `draft.json`。
- 运行产物可含二进制，独立于 result.json 的 JSON 合法性保留。

## 3. result.json 字段

1. 原样回填 `output_contract` 的**全部键**：缺值给 `null`/`[]`/`{}`，不改名、不删未知键；
   缺 `output_contract` 时回退到七字段 `case_id,status,editable_source,export_artifact,
   content_map,compliance_checks,exceptions`。
2. 附加统一信封：`sources` / `uncertainty` / `pending_human` / `errors` / `degradations` /
   `change_log` / `runtime`。
3. 本版强化字段 `evidence_map`（细粒度证据映射），结构见下。

`status` 取值：`ok`（完整可交付）/ `partial`（部分，须写明 limits）/
`insufficient_evidence`（材料不足，不编造）/ `input_error`（输入缺失或坏 JSON）。

## 4. evidence_map（本版侧重点：证据溯源强化）

每条**页级结论**都必须挂 locator + 逐字 quote，数值/单位不得静默改写：

```json
{
  "page_id": 3, "role": "result",
  "claim": "一句话主张",
  "numeric_inference": false,
  "evidence": [{
    "source_id": "materials/paper.pdf",
    "locator": "p7 §3.2 Fig.3",
    "quote": "逐字原文片段（含数字与单位，未改写）",
    "kind": "figure|table|formula|data|text|schematic|code|supplement",
    "fidelity": "verbatim|paraphrase|numeric_inference|inference",
    "uncertain": false,
    "note": "冲突/缺失时在此说明"
  }]
}
```

- `kind` 区分**实验数据**（figure/table/data）与**示意图**（schematic），防止把示意图当
  实验数据展示；无法判断写 `schematic` 并标 `uncertain=true`。
- `fidelity` 说明该 quote 与 claim 的关系：逐字/改写保留数值/数值推断/推断。
- claim 中的数字+单位必须逐字出现在某条 quote 中，否则必须 `numeric_inference=true`
  （脚本 `validate` 会按此警告）。
- 材料未报告/读不出 → 写 `uncertainty`/`pending_human`，绝不编造。

## 5. 六个失败模式 → 对应机器检查（scripts/main.py validate）

| 失败模式 | 机器可查项 |
|-|-|
| 段落搬家、主线丢失 | 每页 `role` 合法、`claim` 非空、页序按主线（人工复核） |
| 图表编号错位 | `figure_refs` 与 source 内实际编号逐字一致（quote 含编号） |
| 示意图当数据 | `evidence.kind` 显式标注，figure/table/data 才作实验证据 |
| 数值单位改写 | `numeric_inference` 标记 + validate 的 `numeric_not_verbatim` 警告 |
| 备注复读页面 | `speaker_notes` 不得与正文逐字重复（`notes_duplicate_body`） |
| 模板/字体崩坏 | pptx 继承模板 master/layout/theme；PDF 页数 == pptx 页数 |
