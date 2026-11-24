# 字段字典与规则（fields-and-rules）

本文件规定管道使用的确定性规则、中间表示（IR）结构、目录/清单解析字段与
`decisions.json` 覆盖层写法。目标是让任何人在无作者环境里按同一规则复跑。

## 1. 中间表示（IR）

管道在 `/app/work/inventory.ir.json` 写入一份规范 IR，所有分析阶段都是对 IR 的
纯函数；`/app/work/decisions.empty.json` 是给 Agent 填写的决策骨架。

顶层键：`schema_version`、`meta`、`contract`、`contract_keys`、`discovery`、
`entries`、`catalogs`、`tree`、`duplicates`、`versions`、`aliases`、
`version_notes`、`reconciliation`、`index`、`answers`、`errors`、
`degradations`、`limitations`、`pending_human`、`sources`、`uncertainty`、
`change_log`、`artifact_paths`、`runtime`。

## 2. 条目字段（entries[]）

| 字段 | 说明 |
|---|---|
| `path` | 相对路径，统一 `/` 分隔，用于排序键 |
| `kind` | `file`/`dir`/`symlink`/`broken_link`/`special` |
| `size` | 字节数（目录为 0） |
| `mtime`/`mode` | 修改时间与权限位；`mode` 为 `stat.S_IMODE` |
| `sha256` | 内容哈希；超大/不可读为 `null` |
| `content_kind` | `pdf`/`text`/`json`/`csv`/`bibtex`/`ris`/`sqlite`/`binary`/`unknown` |
| `encoding` | `utf-8-sig`/`utf-8`/`latin-1`/`skipped:too_large`/`unreadable` |
| `flags` | `too_large`/`unreadable`/`non_regular`/`symlink`/`broken_link`/`bad_encoding` |
| `link_target` | 符号链接目标（只读记录，不跟随） |
| `inode` | `[st_dev, st_ino]`，仅当 `st_nlink>1` 时写入，用于硬链接分组 |

## 3. 判定规则

- **去重**：按 `sha256` 分组，`len(paths)>1` 才记为重复组；不按文件名/扩展名。
- **版本链**：`_version_stem()` 去掉扩展名后，用正则去除版本记号
  （`v1`/`draft`/`final`/`rev`/`copy`/`backup`/`old`/`new`/日期/`(1)` 等），
  再归一化为小写字母数字主干；按 `(parent, stem)` 分桶，桶内按 `mtime` 升序
  生成候选链，相邻成员记录 `size_delta` 与 `same_content`，并标 `heuristic=true`。
- **别名**：符号链接按 `link_target` 分组；断链单列 `broken_links`；硬链接按
  `inode` 分组（Windows 下 `st_ino` 不可靠时标记 `hardlink_detection=unavailable`）。
- **对账**：目录记录先按 `path`/`file`/`filename`/`name`/`filepath`/`relpath`/`id`
  精确匹配文件系统，再按唯一 basename 回退匹配；比较 `size` 与 `sha256`（或
  `sha256sum`/`hash`），得出 `matches`/`mismatches`/`missing`/`orphaned`。
- **版本说明**：三路来源——case.json 的 `version_notes`/`versions`/
  `version_history`/`administrative_versions`；task.md 中的关键词上下文；
  目录记录中的 `replaces`/`supersedes`/`version`/`revision`/`parent`/
  `superseded_by`/`revision_of`。全部保留 `source` 出处。

## 4. 目录/清单解析字段

- JSON：顶层列表，或 `files`/`entries`/`items`/`records`/`manifest` 列表，或单对象。
- CSV/TSV：`csv.Sniffer` 探测分隔符，`DictReader` 读列头。
- BibTeX：`@type{key, ...}`，字段小写（`title`/`author`/`year`/`journal`/`doi`…）。
- RIS：`TY`…`ER` 块，两字母标签小写。
- SQLite：只读打开 `sqlite_master` 枚举表，逐表 `SELECT *`；失败记
  `sqlite_open_error`/`sqlite_query_error`。
- 所有记录带 `_catalog`（来源文件路径）与 `_index`（行序），字段出处可回溯。

## 5. decisions.json 覆盖层

Agent 的语义结论以数据形式覆盖到最终 result.json，不进入代码路径：

```json
{
  "contract": {"<contract字段>": "<按题意的最终值>"},
  "notes": [
    {"item": "version_chain_confirm", "decision": "linked",
     "paths": ["materials/a.pdf", "materials/b.pdf"],
     "sources": [{"item": "版本说明", "locator": "task.md",
                  "quote": "draft-v1 被 draft-v2 取代"}]}
  ],
  "uncertainty": [{"item": "...", "reason": "...", "severity": "low|medium|high"}],
  "pending_human": ["..."]
}
```

`contract` 之外的键会被 `_build_result` 忽略；每个 `notes` 条目必须带
locator+quote 证据。覆盖层合并不改动机械层的确定性结论，只补充语义判定与
contract 字段值。

## 6. 保留键冲突

result.json 的保留键为 `case_id`/`status`/`sources`/`uncertainty`/`change_log`/
`errors`/`degradations`/`runtime`/`limitations`/`pending_human`/`computed`/
`artifacts`/`decisions`。若题目 contract 出现同名键，其值写入
`computed._contract_<字段>`，不改名、不删除。
