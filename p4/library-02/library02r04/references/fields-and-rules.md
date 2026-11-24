# 字段与规则说明（fields-and-rules）

本文件是脚本与 Agent 共享的字段约定与判定规则。脚本 `scripts/core_impl.py` 按此实现，Agent 语义层按此补证据，二者不得冲突。

## 1. result.json 保留键（脚本自有，不可被 contract 字段覆盖）

`case_id`、`status`、`sources`、`uncertainty`、`change_log`、`computed`、`errors`、`degradations`、`runtime`、`limitations`、`pending_human`。

若题目 `output_contract` 中某字段与上述键同名，原样改存到 `computed._contract_<字段>`，不得丢弃、不得改名其它键。

## 2. 置信分级（confidence）

| 级别 | 含义 | 触发依据示例 |
|---|---|---|
| `certain` | 可由机器确定地复核 | 内容 SHA-256 相同、lstat/readlink 确定符号链接、逐字节相同 |
| `high` | 逐字证据可复核，且定位无歧义 | pdftotext 命中原段落并给出页码+逐字 quote |
| `medium` | 较强但有推断成分 | 同义改写命中、归一化主干+文本相似度候选 |
| `low` | 弱证据或启发式 | 只命中文件名、机械词项重叠、无内容校验 |
| `unknown` | 无法验证 | 无文本层、材料缺失、坏编码、条件不满足 |

任何结论都必须带 `confidence`；低于 `medium` 的结论不得当作事实写进主结果，只能作为候选或放入 `uncertainty`/`pending_human`。

## 3. 降级码（degradations.code）与严重度

| code | 含义 | severity |
|---|---|---|
| `too_large` | 超过 20MB，跳过内容哈希 | warning |
| `unreadable` | 无权限/读取失败 | warning |
| `non_regular` | 非常规文件（FIFO/设备等） | info |
| `symlink` | 符号链接，不哈希内容 | info |
| `sqlite_open_error` / `sqlite_query_error` | SQLite 打不开/坏表 | warning |
| `unreadable_manifest` | 清单文件不可读 | warning |
| `no_text_layer` | 扫描件无文本层 | warning |
| `filter_unsatisfied` | 过滤条件无法满足 | warning（不放宽） |

`errors` 用于"应当完成但失败且影响结论"的事件；`degradations` 用于"被跳过但仍继续"的事件。

## 4. 证据溯源格式

`locator` 一律用**相对输入根**的路径 + 定位后缀：

- 文本/代码：`materials/protocol.md#L12`（行号）或 `#段落首句`
- PDF：`materials/paper.pdf#p3`（物理页）或 `#p3-印刷页97`
- 扫描件：`materials/scan.pdf#p4-ocr`
- 库目录条目：`library.csv#row7`

`quote` 必须是从材料中**逐字**摘录的片段（≤320 字符），不得改写；无法摘录时写 `not_reported`，不得自拟。

## 5. 版本链判定规则

1. 归一化主干：去扩展名 → NFKC 小写 → 去日期 token（`YYYY[-_]MM[-_]DD`）→ 去版本 token（`v/ver/version/rev/r` + 数字、`draft/final/copy/old/new/latest/clean/updated/revised/review`）→ 非字母数字转空格 → 折叠空白。
2. 相同主干按内容相似度成对给候选：SHA-256 相同 = `certain`（实为重复）；文本 `SequenceMatcher.ratio` = `medium`；无内容可比 = `low`。
3. 所有候选标 `heuristic: true`，必须由 Agent 复核后才能写为确定的版本关系。

## 6. 恢复对账（reconciliation）

- `matches`：清单列出且实际存在、路径一致的项。
- `mismatches`：清单列出、实际存在但路径/名称不完全一致。
- `missing`：清单列出但实际不存在。
- `orphaned`：实际存在但任何清单都未登记（排除 `case.json`/`task.md`）。
- 无机器可读清单时 `confidence=low`，并在 `uncertainty` 说明对账是近似值。

## 7. 可编辑产物清单

| 文件 | 内容 |
|---|---|
| `library_manifest.csv` | 每行一个源文件：路径/名称/父目录/类型/大小/mtime/权限/sha256/符号链接/硬链接数/hash 状态 |
| `directory_tree.txt` | 目录树（空目录标 `[空目录]`） |
| `dedup_version.json` | 重复组、版本候选、版本说明、别名 |
| `recovery_report.json` | 对账 + errors + degradations |
| `library_index.json` | 文件条目、倒排词项、版本/别名/对账摘要 |
| `library_manifest.md` | 交接报告 |
| `operation_log.jsonl` | 操作日志（每行一条 JSON） |

## 8. patch.json（Agent 语义层增量合并格式）

```json
{
  "status": "ok",
  "sources": [{"item":"...","locator":"...","quote":"...","confidence":"high"}],
  "uncertainty": [{"item":"...","reason":"...","severity":"...","evidence_basis":"...","mitigation":"..."}],
  "computed": {"retrieval": {"query":"...","hits":[{"path":"...","locator":"...","quote":"...","confidence":"..."}]}}
}
```

`--update` 会合并 `computed.*` 与上述顶层字段；contract 字段直接以顶层键写入。禁止在 patch 中编造任何未从材料读到的数值或文本。
