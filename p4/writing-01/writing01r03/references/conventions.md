# 契约与规范（Conventions）

本文件定义 result.json 的字段语义、证据定位格式、引用格式与确定性约定。脚本与其保持一致。

## 1. result.json 顶层字段

| 字段 | 类型 | 含义 |
|-|-|-|
| `case_id` | string | 来自 case.json 的 `id`/`case_id`/`caseId`；缺省 `case.json` |
| `status` | string | `in_progress` / `partial` / `complete` / `insufficient_evidence` / `input_error` / `failed` |
| output_contract 字段 | 原样回填 | 值类型以题面为准，先 `null` 再逐字段填充，不改名、不删未知键 |
| `sources` | array | 每条事实/引用的来源，元素含 `id`、`locator`、`quote`、`ref_id`、`note` |
| `uncertainty` | array | 无法确认、冲突、缺失项，元素含 `item`、`kind`、`status` |
| `pending_human` | array | 需人工确认/补录项 |
| `change_log` | array | 每次改动，元素含 `seq`（单调递增）、`stage`、`note` |
| `degradations` | array | 降级记录（坏 JSON、坏编码、缺材料、超长等） |
| `runtime` | object | 预算快照：900s / 64 请求 / 16384 token，仅记录不参与评分 |

### 状态语义

- `in_progress`：骨架已落盘，稿件尚未完成；
- `partial`：已产出可编辑稿件，但存在缺项/缺材料/缺解析，已在 `uncertainty` 说明；
- `complete`：稿件、来源映射、版本边界全部齐备且可复核；
- `insufficient_evidence`：输入无可用材料，只能说明缺口；
- `input_error`：case.json 损坏或关键输入无法解析；
- `failed`：脚本级异常（保留骨架，如实记录原因）。

## 2. 证据定位（locator）格式

正文事实断言用 `[S:材料ID#位置]`，`材料ID` 是 `material_index.json` 里的 `m1`、`m2`……（按材料相对路径字典序分配）。位置粒度任选：

- `[S:m1]`：材料级；
- `[S:m1#p3]`：第 3 页；
- `[S:m1#L12-L15]`：第 12–15 行；
- `[S:m1#tab2]` / `[S:m1#fig1]`：表/图。

每条来源必须在 `material_index.json` 的 `key_claims` 中记录 `locator` 与逐字 `quote`，二者按 locator 精确匹配。`quote` 是从材料原样复制的片段，不得改写或拼接推断内容。

## 3. 引用键（[C:]）格式

- 正文用 `[C:键]`；键用小写作者姓+年份，如 `[C:smith2020]`。
- 文末参考文献区每行以 `- [C:键]: 作者. 题目. 期刊 年份;卷(期):页. DOI` 定义。
- `evidence_map.py` 以行首 `- [C:键]:` 识别定义，正文出现即视为使用。
- 缺失字段写 `unknown`；禁止补造 DOI、卷期或页码。取不到全文时只依据给定元数据引用。

## 4. 缺失/冲突标注

| 标注 | 含义 | 落盘位置 |
|-|-|-|
| `[缺失]` | 图注/声明字段/章节缺失 | 正文原位 + `uncertainty` |
| `[待确认]` | 需要人工确认或材料外补写 | 正文原位 + `pending_human` |
| `[冲突待确认]` | 材料间数值/口径冲突 | 正文原位 + `sources[].conflict` |
| `[无法解析]` | PDF/OCR/图表解析失败 | 正文原位 + `uncertainty` |

## 5. 确定性约定

- 所有 JSON 用 `sort_keys=True`、`ensure_ascii=False` 写出，UTF-8、LF 行尾。
- 材料扫描按 `os.walk` 且目录/文件各自字典序排序，材料 ID 分配稳定。
- `change_log` 只写 `seq/stage/note`，不写墙钟时间；同一 `/input` 两次运行的 `result.json`、`material_index.json`、`evidence_map.json` 字节一致。
- `selftest.py` 负责校验以上两点。
