# 字段与规则说明（fields-and-rules）

本文件描述 Skill 运行时各产物的字段含义、判定规则与定位格式，供 Agent 与他人复核。

## result.json 保留字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `case_id` | string | 案例标识，从 case.json 或 task.md 识别，非空 |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error` |
| `sources` | array | 关键结论的扁平证据列表（item + locator + quote） |
| `evidence_map` | object | 细粒度证据映射，见下 |
| `uncertainty` | array | `{item, severity, reason}` |
| `pending_human` | array | 需人工确认项 |
| `change_log` | array | `{time, actor, step, detail}` |
| `errors` / `degradations` | array | 失败与降级，`{item, kind, detail}` |
| `runtime` | object | python 版本、skill 版本、时间戳、耗时、路径、大小上限 |
| `limitations` | array | 已知边界与依赖 |
| `computed` | object | 机械证据（files/summary/duplicate_groups/version_candidates/version_notes/aliases/reconciliation/answers/index/artifacts） |

## output_contract 回填规则

- 从 case.json 的 `output_contract` / `output` / `result_schema` / `contract` 自动识别。
- 全部字段**原样回填到 result.json 顶层**，缺值按 schema 类型给 `""`/`null`/`[]`/`{}`；
  不改名、不删未知键。
- 与保留字段同名时，改存 `computed._contract_<字段>`。
- 无 contract 时，仅产出元数据字段，并在 `uncertainty` 标注 `output_contract` 缺失。

## 判定规则

- **去重**：只按文件内容 SHA-256 判定；文件名相同但内容不同不算重复。
- **版本候选**：归一化主干（剥离 `v1`/`_rev2`/`副本`/`final` 等版本 token 后的小写
  主干）相同，且组内至少 2 个文件，按 `difflib.SequenceMatcher` 计算内容相似度；
  **始终标 `heuristic: true`**，由 Agent 结合版本说明定稿。
- **别名**：`os.readlink` 得符号链接目标（含断链）；`(st_dev, st_ino)` + `st_nlink>1`
  得硬链接组。
- **对账**：目录清单（JSON/CSV/Markdown 表/BibTeX/RIS/SQLite）中的 path/hash 与
  实际盘点比对，产出 `matches / mismatches / missing / orphaned`。

## 定位格式（locator）

- 文本/抽取文本：`相对路径#L12`（第 12 行）；无行号时 `相对路径#o偏移`。
- PDF：`相对路径#p3`（物理页，需文本层；无文本层不编造页码）。
- `quote` 为定位处的逐字子串；`quote_span` 为该子串在抽取文本中的 `[起,止)` 字符区间；
  `source_sha256` 为产生该 quote 的文件内容 SHA-256。

## evidence_map 结构

```json
{
  "conclusions": [
    {
      "id": "C0001",
      "claim": "…结论陈述…",
      "type": "inventory|duplicate|version|version_note|alias|reconciliation|answer",
      "verdict": "verified|inferred|conflict|unverified",
      "status": "ok|partial|…",
      "confidence": "high|medium|low",
      "method": "…判定方法…",
      "locators": [
        {
          "locator": "materials/protocol.txt#L1",
          "quote": "缓冲液配方: 10 mM Tris, pH 8.0",
          "quote_span": [0, 30],
          "source_file": "materials/protocol.txt",
          "source_sha256": "…64位十六进制…",
          "page": null,
          "line": 1
        }
      ]
    }
  ]
}
```

## 文本抽取方法（脚本内置，纯标准库）

- 文本类：`utf-8-sig → utf-8 → gb18030 → latin-1` 容错，坏编码标 `bad_encoding`。
- PDF：naive `stream/endstream` + zlib 解压 + `Tj/TJ` 文本算子；失败标
  `pdf_no_text_layer` / `pdf_parse_error`，由 Agent 调用平台 pdf-text-tool 补抽。
- Office（docx/xlsx/pptx）：ZIP 容器内 XML 提取文本。
- 归档（zip/tar/gz…）：跳过内容，标 `archive_skipped`。
- 超大文件（>2 MiB）：跳过内容哈希，保留路径/大小/权限，标 `too_large`。
