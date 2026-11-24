# 字段与规则说明（fields-and-rules）

本文件定义 Skill 的输入/输出字段、定位规则、版本/别名/对账语义与确定性契约，供 Agent
在语义归位阶段对照使用，也供他人复跑核对。

## 1. 输入发现（先发现，再假设）

- 输入根目录默认 `/input`；`case.json` 的识别顺序为 `case.json`/`input.json`/`task.json`，
  `task.md` 的识别顺序为 `task.md`/`README.md`/`instructions.md`，均按固定顺序发现。
- `output_contract` 的候选键名（按序）：`output_contract`、`output`、`result_schema`、
  `contract`、`output_schema`、`result`；若其值为 JSON 字符串则先解析为对象。
- 文本读取顺序 `utf-8-sig` → `utf-8` → `gb18030` → `latin-1`；坏编码记录 `bad_encoding` 并
  用 `utf-8(replace)` 降级，不中断。

## 2. 输出字段（result.json）

保留顶层键：`case_id`、`status`、`task_title`、`challenge_id`、`task_path`、`input_root`、
`output_dir`、`work_dir`、`generated_at`、`runtime`、`sources`、`uncertainty`、
`pending_human`、`change_log`、`errors`、`degradations`、`limitations`、`computed`、
`output_contract`。

- `status` 取值：`ok`（全部合同字段已填且证据齐全）/ `partial`（已落盘但仍有语义待补）/
  `insufficient_evidence`（材料不足）/ `input_error`（输入不可解析）。
- `computed` 子键：`files`、`dirs`、`summary`、`duplicate_groups`、`version_candidates`、
  `version_notes`、`aliases`、`reconciliation`、`answers`、`index`、`artifacts`。
- `sources[]` 条目：`{item, locator, quote}`；`quote` 必须是逐字原文，不能改写。
- `change_log[]` 条目：`{seq, actor, step, detail, time}`；`time` 为本次运行的同一时间戳，
  属易变字段，确定性比对时忽略。

### 2.1 文件记录字段（computed.files[]）

`path`（相对输入根、正斜杠）、`name`、`ext`、`type`（file/dir/symlink/non_regular）、
`size`、`mtime`（整数秒）、`mode`（八进制）、`nlink`、`sha256`、`is_symlink`、
`link_target`、`hardlink_group`、`version_stem`、`text_layer`、`text_preview`、
`degradation`、`error`。

- 符号链接：`type=symlink`、`is_symlink=true`；目标不存在记 `degradation=broken_symlink`。
- 硬链接：按 `(st_dev, st_ino)` 分组，`nlink>1` 且同组 ≥2 才入 `hardlink_groups`。
- 超大文件（>64 MiB）：跳过哈希，记 `degradation=too_large`。
- 非常规文件（套接字/设备等）：`type=non_regular`，记 `degradation=non_regular`。

## 3. 定位规则（locator）

- 文件定位：相对输入根路径，如 `materials/proj/protocol_v2.pdf`。
- 页/段定位：文本文件用 `#L<行号>`；PDF 用 `#pdf-text`（标准库启发式提取，非打印页码）；
  Agent 用平台 pdf-text-tool 确认打印页码后，写入 `materials/x.pdf#p<印刷页码>` 并保留原文片段。
- 证据回链四条要求：文件路径、版本标识、页码/段落、段落首句 + 原文片段，缺一不可。

## 4. 版本与去重语义

- 去重：按内容 SHA-256，组内 `paths` 为同内容文件集合，与文件名无关。
- 版本候选：按 `normalize_stem`（剥离版本/副本/日期后缀、去符号、小写）分组，组内两两
  文本相似度用 `difflib.SequenceMatcher`（超大文本退化为分词 Jaccard）；结果标 `heuristic: true`，
  是候选而非结论。
- 归一化主干：`report_v2`、`report-final`、`report copy` → `report`；仅含版本号的裸名
  （`v1`/`v2`）归并到 `_versioned_only_`。

## 5. 别名语义（aliases）

- `symlinks`：全部符号链接路径（含断链）。
- `broken_symlinks`：目标不存在的符号链接。
- `hardlink_groups`：`{dev, ino, paths[]}`，硬链接别名组。

## 6. 恢复对账语义（reconciliation）

以库目录清单（manifest）声明的 `path`/`hash`/`version` 为期望值，与实际盘点比对：

- `matches`：声明路径存在且（若给哈希）哈希一致。
- `mismatches`：路径存在但哈希不一致（记 `source_conflict` 语义，需补 `sources`）。
- `missing`：声明路径在盘点中不存在。
- `orphaned`：实际文件未被任何 manifest 声明的路径覆盖。
- `manifest_files`：每个被解析的 manifest 的路径、类型、记录数与解析错误。

## 7. 确定性契约

- 所有目录遍历 `os.walk` 的 dirnames/filenames 排序；所有分组键排序；集合迭代前转
  `sorted`；JSON 序列化 `sort_keys=True`、`ensure_ascii=False`、换行 `\n`。
- 不使用 `random`；哈希为稳定 SHA-256；时间戳只出现在 `generated_at`、`runtime.seconds`、
  `change_log[].time` 三个位置。
- 确定性自检：`scripts/selftest.py` 连跑两次，比对 `result.json`（剥离易变键）与其余产物
  逐字节；易变键 = `generated_at`/`time`/`seconds`/`started_at`/`finished_at`/`duration`/
  `wall_time`/`mtime`/`output_dir`/`work_dir`。

## 8. 检索问答

- 机械问答由脚本产出：盘点统计、重复组、断链、对账、版本候选、难处理文件。
- 案例显式提问（从 case.json 的 `questions/queries/asks` 或 task.md 的"检索问题"段落提取）
  先以 `status=pending_human` 挂起，由 Agent 用 `--update` 补语义答案与 `sources`。
- 过滤条件（年份/项目编号/文件类型/批次/版本/权限）作为硬约束执行；无法满足时如实报告，
  不放宽。
