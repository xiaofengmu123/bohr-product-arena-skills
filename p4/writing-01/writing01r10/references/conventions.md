# 契约与中间表示规范（conventions）

本文件定义三个契约：输入发现、证据目录（IR）、result.json 输出。所有脚本与
SKILL.md 都以本文件为唯一口径，改动须同步更新版本号。

## 1. 输入契约（先发现，再假设）

- 运行时不假设固定文件名/键名：`/input/case.json`、`/input/task.md` 与材料文件
  （PDF/图片/CSV/docx/补充文本）均通过扫描发现。
- 文本按 `utf-8-sig → utf-8 → latin-1` 依次读取并容错；坏 JSON 记录到
  `errors`/`degradations`，不崩溃。
- 单材料文件上限 2MB，超限跳过并在 `errors` 记录。
- `case.json` 的 `id`/`case_id`/`challenge_id`/`task_id` 任一作为 `case_id`，
  全部缺失时回退为文件名主干，仍缺则 `unknown`。
- PDF 必须真实解析（pdftotext/pdftoppm），图片表格用 OCR/渲染；不凭文件名或
  领域标签补造内容。

## 2. 证据目录 evidence_catalog.json（唯一中间表示 IR）

路径：`/app/work/evidence_catalog.json`。这是本包与"先写稿后检查"流程的关键差异：
事实、引用、材料三者都写入同一份带 schema 的目录，脚本只读写它，避免多份索引漂移。

```json
{
  "schema": "evidence-catalog/v1",
  "case_id": "…",
  "materials": [{"id": "M01", "path": "…", "kind": "pdf", "status": "parsed"}],
  "claims": [
    {
      "id": "K01",
      "kind": "fact|number|figure|table|citation_claim|term|method",
      "section": "results",
      "text": "正文中的事实性表述",
      "locator": "materials/paper.pdf#p3",
      "quote": "逐字引用的原文片段",
      "citation_keys": ["C01"],
      "confidence": "verbatim|inferred|conflict|unconfirmed",
      "flags": []
    }
  ],
  "citations": [
    {"key": "C01", "authors": "…", "year": "…", "title": "…",
     "journal": "…", "volume": null, "issue": null, "pages": null,
     "doi": null, "status": "verified|metadata_only|missing", "raw": "材料原文字段"}
  ],
  "conflicts": [],
  "gaps": []
}
```

硬规则：

- 每条 `claim` 必须有非空 `locator` 与非空 `quote`（逐字），否则
  `evidence.py check` 判 `E_CLAIM_NO_QUOTE` / `E_CLAIM_NO_LOCATOR`。
- `citation_keys` 中的每个键必须在 `citations` 有定义；`doi`/`volume` 取不到
  一律 `null`，不杜撰。
- 同一指标两个来源不一致时，两边都记录并放入 `conflicts`，标 `source_conflict`。

## 3. 稿件内标记约定

- 事实来源：`[S:材料ID]`，如 `[S:M02]`；材料 ID 对应材料清单。
- 引用：`[C:键]`，如 `[C:C03]`；文末参考文献区用 Markdown 引用定义行定义：
  `[C:C03]: Author. Title. Journal Year;vol(issue):pages. DOI`
- 缺失/待核：`[缺失]`、`[待确认]`、`[冲突待确认]`、`[无法解析]`。
- 这些标记同时是 `evidence.py check --draft` 的扫描目标。

## 4. result.json 输出契约

路径：`/app/output/result.json`。顶层必须包含：

- `case_id`：与 `/input/case.json` 一致；
- output_contract **全部字段**：以 case.json 的 `output_contract` 为准，缺省为
  `deliverable`、`fact_source_map`、`version_limits`；字段值指向 `/app/output/`
  下的可编辑产物路径；
- `status`：`in_progress | partial | complete | insufficient_evidence | input_error | failed`；
- `sources`：每条事实/引用的来源（材料位置 + 文献条目）；
- `uncertainty`：无法确认、冲突、缺失、需人工确认项；
- `pending_human`：必须由作者确认的事项；
- `change_log`：每次改动的 `ts`/`step`/`note`；
- `errors` / `degradations`：运行错误与降级说明；
- `runtime`：时间预算、请求预算、token 预算（可核验）。

主稿件写 `/app/output/manuscript.md`（保留层级、图注编号、引用定义行）；要求
docx/xlsx/pptx 时另渲染真实 PDF/PNG 预览供检查。

## 5. status 语义

| 值 | 含义 |
|-|-|
| `in_progress` | 骨架已落盘，正在增量抽取/撰写 |
| `partial` | 部分完成，缺项已显式标注 |
| `complete` | 契约字段齐备、无未清项、一致性检查零 error |
| `insufficient_evidence` | 无可用材料，不编造 |
| `input_error` | 输入缺失/不可解析 |
| `failed` | 内部脚本异常，保留已落盘骨架并记录原因 |

## 6. 学科章节模板（可复用，IMRaD 类通用）

生成时按此归位，缺项显式标注 `[缺失]`，不逐字照抄、不用素材堆叠。学科/期刊另有
要求时以题目 output_contract 为准。

- **标题页**：标题、作者、机构、通讯信息；材料缺失标 `[缺失]`。
- **摘要**：目的、方法、主要结果（数值 + 单位 + 样本量）、结论；数字全挂 `[S:]`。
- **引言**：背景、已有工作（`[C:]`）、研究缺口、本文目标，避免论证跳跃。
- **方法**：材料、仪器、实验步骤、统计方法；样本量与显著性阈值逐条溯源。
- **结果**：按图/表顺序叙述，正文与图注一致；图注统一 `Figure N. 描述`。
- **讨论**：解释结果、与文献对照、局限；局限写入 `uncertainty`。
- **结论**：主结论 + 影响，不新增结果。
- **参考文献**：`[C:键]: 作者. 题目. 期刊 年份;卷(期):页. DOI`，一条一定义。
- **数据可得性声明**：`data availability` 段；材料无则写 `[缺失]`。
- **补充材料**：材料中存在才写，否则不新增。

## 7. 复用与维护

- 更换学科时只需替换第 6 节模板与 checklist.md 的核心章节表，脚本与 IR 不动。
- 脚本均为纯标准库、无第三方依赖；任何新改动须先 `python3 scripts/selftest.py --selftest`。
- 版本号变更记录在 SKILL.md frontmatter，契约 schema 变更须同步升级
  `evidence-catalog/v1` 与 `material-manifest/v1` 的版本号。
