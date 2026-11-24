# 字段级契约与中间表示（IR）规范

本 Skill 采用三层中间表示（IR）把「事实」与「判断」物理分离，保证每一步可复跑、可核验。

## 1. 三层 IR

| 层 | 文件 | 产生者 | 内容 | 确定性 |
|---|---|---|---|---|
| 事实层 | `/app/work/facts.json` | `scan` | 字节级事实：文件、大小、sha256、类型、编码、权限、文本层、case 元数据 | 是 |
| 推断层 | `/app/work/inferences.json` | `infer`（scan 内部调用） | 重复/版本/别名/冲突分组、显式关系、倒排索引 | 是 |
| 判定层 | `/app/work/judgments.json` | 模型书写 | 行政版本备注、别名裁定、检索答案，每条带 evidence | 否（模型输入） |
| 校验报告 | `/app/work/validation_report.json` | `adjudicate` | 逐条判断的证据落地结论 | 由 judgments 决定 |

`work/text/<source_id>.txt` 存放确定性引擎抽出的文本层（≤4MB）；模型可用外部工具
（pdftotext/OCR/office-renderer）补充 `work/extracted/<source_id>.txt`，`adjudicate` 会优先
用补充文本做 quote 落地，使 PDF/扫描件也能被校验而不编造。

## 2. 定位规范（locator）

- 文件定位：相对输入目录的 POSIX 路径（如 `materials/paper.pdf`），不写绝对路径。
- 哈希定位：sha256 十六进制小写全文（`facts.json`）或前 8/16 位（清单展示）。
- 文本内定位：quote 前可加 `#L<n>` 行号前缀；`adjudicate` 先做全文子串命中，再做行号命中。
- 证据对象固定为 `{source_id, locator, quote, sha256?}`；`source_id` 必须是 `facts.json`
  中的 `Fxxx` 或 `case`（指 case.json 本身）。

## 3. 状态语义

| status | 含义 |
|---|---|
| `ok` | 文件盘点完成，judgments 已提供且全部证据落地（无 quarantined） |
| `partial` | 至少一版合法产物已落盘，但存在未落地判断、无判定层或部分缺口 |
| `insufficient_evidence` | 输入目录无可盘点材料 |
| `input_error` | case.json 缺失/不可解析；若仍有材料，则继续盘点并附错误记录 |

判定粒度状态：`validation_status = ok | quarantined | degraded_no_text`。
`quarantined`（证据未落地）的判断不并入 `claims`，转 `pending_human`。

## 4. 输出契约（result.json）

`result.json` 必须包含：

1. `case_id`：从输入原样带回，不改写；
2. `output_contract` 全部字段：dict/list 双形态，逐键回填；按字段名启发式映射到
   确定性结果（`file*/inventory*`→文件清单，`dup*/alias*`→别名组，`version*`→版本组，
   `conflict*`→冲突，`query*/answer*/search*`→检索答案，`index*`→索引统计，
   `meta*`→元数据，`claim*`→claims，`source*`→sources，`change*/log*`→change_log，
   `uncertain*`→uncertainty），无法映射的键填 `null`/`[]`，不改名、不删未知键；
3. 信封字段：`status`、`sources`、`uncertainty`、`change_log`、`pending_human`、
   `errors`、`degradations`、`runtime`；
4. 另附：`result`（盘点摘要与分组）、`claims`（统一 claim 模型）、`uncertainties`。

## 5. 证据溯源硬要求

- 每条结论（重复、版本、别名、检索命中、跳过）都要能指回材料的逐字片段
  （`source_id + locator + quote`）。
- 冲突信息不隐藏：同一 stem 两个哈希不一致时都记录并标 `source_conflict`，进
  `pending_human`，不擅自裁定。
- 缺值写 `pending_human` 或 `not_reported`，不虚构引用、DOI、数值。
- 无文本层文件按字节级证据处理，quote 落地标 `not_text`，不假装有文本。

## 6. change_log 条目

每条含 `op`、`before`、`after`、`reason`、`locator`、`source_id`；`op` 取值
`merge_duplicates / link_versions / flag_conflict / record_relation / apply_judgment /
quarantine_judgment`。
