# 规范：契约与证据溯源（Conventions）

本文定义 result.json / material_index.json / evidence_map 的字段、`[S:]`/`[C:]` 标记语法、
locator 与逐字 quote 的书写规则。脚本 `scripts/main.py` 按本文约定校验，Agent 撰写时须遵守。

## 1. result.json（顶层契约）

顶层键 = `case_id` + **output_contract 全部字段** + 固定附加字段。

- `case_id`：来自 `/input/case.json` 的 `id` / `case_id`；两者都没有时用文件名主干，再没有写 `unknown_case`。
- output_contract 字段：以 case.json 的 `output_contract` 的键名为准，**原样回填**，未知键不删、不改名、缺失值给 `null`/`[]`。未定义 `output_contract` 时默认三个字段：
  - `deliverable`：主产物描述 `{"path": str, "format": "markdown|docx|xlsx|pptx|...", "rendered": []}`；未产出时 `null`。
  - `fact_source_map`：事实-来源映射，指向 `/app/work/evidence_map_validation.json` 或内嵌摘要；未产出时 `null`。
  - `version_limits`：版本与边界 `{"diff": [], "pending_human": [], "gaps": [], "anomalies": []}`。
- 固定附加字段：
  - `status`：`complete | partial | insufficient_evidence | input_error | failed`（见 §5）。
  - `sources`：每条事实/引用的来源 `[{"claim_id": str, "locator": {...}, "quote": str, "citations": [...]}]`。
  - `uncertainty`：无法确认、冲突、缺失、需人工确认项 `[{"what": str, "status": str, "evidence": str}]`。
  - `pending_human`：需人工决断的清单 `[{"what": str, "why": str}]`。
  - `change_log`：每次改动 `[{"ts": str, "step": str, "note": str}]`。
  - `errors` / `degradations` / `runtime`：运行错误、降级记录、预算与时间记录。

## 2. 状态语义（status 要诚实）

| status | 含义 |
|-|-|
| `complete` | 主产物完成，全部检查通过，无未决项 |
| `partial` | 已产出骨架/初稿但有未完成或未核项（骨架落盘阶段即为此态） |
| `insufficient_evidence` | 材料缺失/无法解析，无法产出可交付产物 |
| `input_error` | 输入文件缺失或不可读 |
| `failed` | 运行异常，但 result.json 仍被写出 |

不得虚标 `complete`；缺项写 `partial` 并在 `uncertainty`/`pending_human` 说明。

## 3. material_index.json（材料索引，Agent 在 Step 1 填实）

```json
{
  "case_id": "...",
  "version": 2,
  "materials": [
    {
      "material_id": "M1",
      "path": "materials/exp1.pdf",
      "rel_path": "exp1.pdf",
      "kind": "pdf|markdown|text|csv|docx|image|...",
      "parse_status": "parsed|ocr|unparsed|unparseable",
      "claims": [
        {
          "claim_id": "M1-C1",
          "type": "number|unit|sample_size|statistic|figure|table|citation|conclusion|term|procedure",
          "locator": {"path": "materials/exp1.pdf", "page": 3, "span": "L12-L15", "anchor": "p3"},
          "quote": "逐字引用原文片段……",
          "status": "verified|unverified|conflict|missing"
        }
      ],
      "citations": [
        {"key": "ref1", "authors": "Smith J, Doe A", "year": 2020,
         "title": "...", "journal": "...", "volume": 12, "issue": 3,
         "pages": "100-110", "doi": "unknown"}
      ],
      "flags": ["无法解析"]
    }
  ],
  "conflicts": [{"claim_ids": ["M1-C1","M2-C3"], "note": "数值口径冲突"}],
  "gaps": ["缺少图注"]
}
```

### 3.1 locator（定位器，每条 claim 必填）

- `path`：材料在输入中的相对路径（可解析时）。
- `page`：PDF 页码 / 图片页码；无页码写 `null`。
- `span`：文本行号或区间，如 `L12-L15`；表格写 `Sheet1!B3`；图写 `Fig2`。
- `anchor`：可选锚点，如 `p3` / `Table 2 第 3 行`。

### 3.2 quote（逐字引用，每条 claim 必填，非空）

- 必须是材料中**原样存在**的字串（含数字、单位、符号），不是转述、不是补写。
- 材料为 PDF/图片时，quote 来自 `pdftotext`/OCR 的**真实输出**；失败则该 claim 不建立，
  改标 `[无法解析]` 并写入 `flags`，**禁止凭文件名或领域标签补字**。
- 材料确实不含该信息时，claim 的 `status=missing`、`quote` 可为空字符串，
  但正文对应处必须标 `[缺失]` 或 `[待确认]`，并计入 `uncertainty`。

## 4. [S:] / [C:] 标记语法

- `[S:claim_id]`：事实溯源标记，指向 material_index 中某条 `claim_id`。**每个数值、单位、
  样本量、显著性、图注、关键结论都必须挂 `[S:]`**，不允许"裸数字"。
- `[C:key]`：引用标记，指向某文献条目 `key`。**每条引用必须在正文使用且支撑所在句**；
  文末"参考文献"区用 `[C:key] 作者. 题目. 期刊 年份;卷(期):页. DOI` 定义。
- 占位标记：`[缺失]`、`[待确认]`、`[冲突待确认]`、`[无法解析]`、`[待核]`，必须与
  `uncertainty`/`pending_human` 一一对应。

## 5. evidence_map_validation.json（脚本 `main.py evidence` 产出）

脚本对稿件做**证据溯源校验**并落盘，结构比参考实现更细：

- `sources[]`：每条 `[S:]` 的校验结果，含 `key / status / type / problems / has_locator /
  quote_len / locator / quote`。`status ∈ verified | dangling | missing_trace`；
  `missing_trace` 表示该 claim 缺 locator 或缺 quote。
- `citations[]`：每条 `[C:]` 的校验结果，`status ∈ verified | dangling | incomplete`。
- `unused_claims[]` / `unused_citations[]`：已定义但正文未使用的条目（可疑装饰性/错配引用）。
- `summary`：verified/dangling/missing_trace/incomplete/unused 计数与 `[缺失]` 等 flags。

## 6. 一致性报告（脚本 `main.py check` 产出）

- `missing_sections`：缺失章节（中英任一门语言命中即视为存在）。
- `figure_gaps` / `table_gaps`：图/表编号缺号。
- `dangling_citations`：正文有 `[C:]` 但索引未定义。
- `result_loadable` / `result_missing_keys` / `uncovered_flags`：收口三查。

## 7. 可重复性与确定性

- 脚本无随机数、无隐式网络、无外部依赖；同一输入两次运行产物一致。
- 单文件解析上限 2MB，超限跳过并记录到 `degradations`。
- 文本按 `utf-8-sig → utf-8 → latin-1` 顺序容错解码；坏 JSON 捕获并记录，不崩溃。

## 8. 最小调用示例（示意，非正式案例结果）

**输入** `/input/case.json`：

```json
{"id": "case-demo-001",
 "output_contract": {"deliverable": null, "fact_source_map": null, "version_limits": null}}
```

**材料** `/input/materials/exp1.md`（示意）：

```text
n=12, p=0.03, mean=2.4 mg/dL (SD 0.3).
```

**Step 0 骨架落盘**（前 3 分钟内）：

```bash
python3 scripts/main.py init --input /input --output /app/output --work /app/work
# -> /app/output/result.json (status=partial)
# -> /app/work/material_index.json (claims/citations 为空，待填)
# -> /app/work/contract_meta.json
```

**Step 1 填实材料索引**（Agent 写回 material_index.json）：

```json
{"claims": [
  {"claim_id": "M1-C1", "type": "sample_size",
   "locator": {"path": "materials/exp1.md", "page": 1, "span": "L1"},
   "quote": "n=12", "status": "verified"},
  {"claim_id": "M1-C2", "type": "statistic",
   "locator": {"path": "materials/exp1.md", "page": 1, "span": "L1"},
   "quote": "p=0.03", "status": "verified"},
  {"claim_id": "M1-C3", "type": "number",
   "locator": {"path": "materials/exp1.md", "page": 1, "span": "L1"},
   "quote": "mean=2.4 mg/dL (SD 0.3)", "status": "verified"}
]}
```

**Step 2 稿件片段**（每条结论挂 `[S:]`，引用挂 `[C:]`）：

```markdown
样本量 n=12 [S:M1-C1]，组间差异显著（p=0.03 [S:M1-C2]），
均值为 2.4 mg/dL（SD 0.3）[S:M1-C3][C:ref1]。

## 参考文献
[C:ref1] Smith J, Doe A. A test study. J. Test 2020;12(3):100-110. DOI unknown
```

**Step 3 校验**：

```bash
python3 scripts/main.py evidence --draft /app/output/manuscript.md \
  --index /app/work/material_index.json --work /app/work
python3 scripts/main.py check --draft /app/output/manuscript.md \
  --index /app/work/material_index.json \
  --result /app/output/result.json --work /app/work
```

`evidence` 报告应显示 `sources_verified>=1`、`sources_dangling==0`、`sources_missing_trace==0`；
若某 claim 缺 quote 或缺 locator，会被标记为 `missing_trace`。以上为示意输出，不冒充正式案例。
