# 中间表示 Schema 与字段规则

本 Skill 的核心设计是**四层结构化中间表示（IR）**：每层带 `schema` 版本号，后层由前层机械推导，`--check` 跨层回验。以下为各层字段约定与证据规则。

## 1. plan.json（任务层，schema `library-retrieval-plan/1`）

| 字段 | 含义 |
|-|-|
| `case_id` | 从 case.json（`case_id`/`caseId`/`id`/`challenge_id`）或 task.md 识别 |
| `contract_key` | output_contract 在 case.json 中的实际键名（自动识别 `output_contract`/`output`/`result_schema`/`contract` 等） |
| `contract` | 原始 output_contract（dict 或 list），原样保存 |
| `goals` | 从 task.md 列表行提取的任务目标（启发式） |
| `filter_dimensions` | 检测到的过滤维度：年份/项目/文件类型/批次/版本/权限/来源/作者/日期 |
| `questions` | 从 case.json（questions/queries/tasks/searches）与 task.md（Q:/问题:/检索:/查询:）提取的待回答提问 |

## 2. inventory.json（数据层，schema `library-inventory/1`）

每个文件记录：`id`、`path`（相对 `/input`，正斜杠）、`name`、`ext`、`role`（control=case.json/task.md，material=其余）、`is_symlink`、`link_target`、`is_broken_symlink`、`hardlink_group`、`size`、`mtime`、`mode`（八进制）、`file_type`（text/pdf/office/image/binary/symlink/special/unknown）、`sha256`、`sha256_norm`、`read_status`、`encoding`、`text_locator`、`line_count`、`error`。

- 文本按 `utf-8-sig`→`utf-8`→`latin-1` 容错读取；`latin-1` 记为 `degraded`。
- 单文件 2 MiB 上限，超限记 `too_large`；符号链接/非常规文件记 `symlink`/`non_regular`。
- PDF 尝试调用系统 `pdftotext`；docx 用 `zipfile` 提取 `word/document.xml`；失败均记 degradation。
- 硬链接组：同 SHA-256 且 `os.path.samefile` 为真时归为同一 `hardlink_group`。

## 3. library_model.json（领域层，schema `library-model/1`）

- `duplicate_groups`：按内容 SHA-256 分组（≥2 个文件），含 `canonical`。
- `version_candidates`：按归一化主干分组，`order_oldest_to_newest` 按 mtime+size 排序，`similarities` 为 token-Jaccard；`method=heuristic` 显式标注。
- `aliases`：`symlink`/`broken_symlink`/`hardlink` 三类。
- `catalogs` + `reconciliation`：解析库目录并对账，区分 `matches`/`mismatches`/`missing`/`orphaned`。
- `index`：词元 + CJK 二元组倒排索引（term → file+lines）；`file_name_index`：文件名索引。
- `degradations`：目录解析产生的坏 JSON 等异常（并入 result.degradations）。
- `heuristics_applied`：列出所有启发式，供复核者判断边界。

### 归一化主干规则（版本链）

1. 小写；`_`、`-` 转空格；去扩展名。
2. 去掉版本号（`v1`/`ver 2.0`/`rev3`）、日期（`2024-05-01`/`20240501`）、`copy`/`副本`/`拷贝`/`dup`、括号。
3. 去掉停用词（`final`/`draft`/`old`/`new`/`backup` 等）。
4. 相同剩余主干且 ≥2 个文件 → 候选版本链。

### 库目录解析与对账规则

- JSON：`json.loads`；list 或含 `files/records/items/entries/documents` 键的 dict 取记录；解析失败记 `bad_json`。
- CSV/TSV：`csv.DictReader`（`utf-8-sig`）。
- Markdown：GFM 表格 + `- key: value` 列表。
- BibTeX：`@type{key, field={value}}`；RIS：`TY/ER` 段。
- SQLite：`sqlite_master` 表名 + 各表前 200 行；打不开记 `sqlite_open_error`。
- 对账：记录中的 `path/file/filename/file_path/location/name` 字段按相对路径或唯一 basename 匹配实际文件。

## 4. evidence_model.json（证据层，schema `evidence-model/1`）

每条 claim：`claim_id`、`kind`（count/duplicate/version/alias/answer/metadata/reconciliation）、`claim`、`sources`（`locator`+`quote`+`verified`）、`confidence`、`status`（verified/pending_human/conflict）。

## 5. result.json（最终）

- 保留键：`case_id`、`status`、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`runtime`、`limitations`、`pending_human`、`computed`。
- output_contract 字段原样回填；与保留键冲突时改存 `computed._contract_<字段>`。
- `computed` 内：`plan`、`files`（紧凑哈希清单，供 `--check` 重算）、`summary`、`duplicate_groups`、`version_candidates`、`aliases`、`reconciliation`、`index_terms`、`answers`、`artifacts`、`evidence`。

## 6. 证据溯源规则（硬要求）

- 每个关键结论给 `locator`（如 `materials/xxx.txt#L12` 或 `materials/xxx.pdf#p3`）+ 逐字 `quote`。
- 冲突不隐藏：同一指标两来源不一致时都记录并标 `source_conflict`。
- 不虚构引用/DOI/哈希/数值；缺失写 `not_reported` 或放入 `pending_human`。
- `--check` 对文本文件 quote 做子串校验、对全部材料重算 SHA-256，失败列出 `WARN`。

## 7. 确定性约定

- 目录遍历与文件排序均使用 `sorted`（相对路径归一化为正斜杠后排序）。
- 无随机数、无字典迭代顺序依赖（Python ≥3.7 插入序）。
- 唯一运行时差异为 `time`/`generated_at_utc` 时间戳；`selftest.py` 剥离时间字段后断言两次运行结果一致。
