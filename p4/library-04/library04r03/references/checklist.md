# 六维度覆盖、证据定位与确定性复核细则

本文件是 SKILL.md 第 6 节自检清单的执行细则，供 Agent 与复核人按同一标准落地。

## 1. 六维度覆盖（对应“结果完整性”）

| 维度 | 必须产出 | 来源 |
|---|---|---|
| 文件类型 | 按扩展名+内容嗅探归类（text/pdf/office/image/archive/unknown） | `inventory.json → summary.file_types` |
| 元数据字段 | Markdown frontmatter 的 title/author/date/tags/doi/version，及 size/mtime/编码 | `inventory.json → files[].metadata / text_encoding` |
| 目录层级 | 相对路径树，含各目录条目 | `inventory.json → directory_tree` |
| 重复文件 | 按 SHA256 分组，列出同哈希全部路径；空文件单列 | `inventory.json → duplicate_groups / empty_files` |
| 版本差异 | 版本基名分组（v1/v2/final/draft/rev/YYYYMMDD），列成员与哈希 | `inventory.json → version_families` |
| 权限与来源 | mode 八进制、可读/可写位；材料来源（task_input 或 case.json 材料列表） | `inventory.json → files[].mode_octal/readable/writable/source` |

## 2. 证据定位规范（locator）

- 文本文件：`<相对路径>#L<起>-L<止>`，单行写 `#L12`。
- PDF：优先 `#p<页码>`（pdftotext 提取后的页号）；无文本层只写 `file+sha256`，标 `unreadable`。
- 引用必须同时给 `file`、`sha256`、`locator`、`quote`；`quote` 为原文逐字片段，禁止改写。
- `sha256` 一律以原文件字节计算；证据落盘后由 `selftest.py` 复核存在性、哈希一致性与逐字命中。

## 3. 不确定性分级

- `certain`：逐字 quote + 单一来源。
- `likely`：由原文推断或摘要，非逐字。
- `conflict`：同一指标两来源不一致，全部记录并标 `source_conflict`。
- `insufficient_evidence`：库内无依据，写明缺什么。
- `unreadable`：文件不可读 / 扫描件无文本层 / 权限不足。

## 4. 版本与重复判定启发式

- 重复：**内容哈希（SHA256）相同**即为重复，不看文件名；空文件单列，不并入哈希组。
- 版本：文件名含 `v1/v2`、`version-`、`rev-`、`final`、`draft`、`YYYYMMDD` 等标记时归入同一版本基名；哈希不同说明内容有差异，`version_families` 保留多条记录供比对。
- 同一基名、同一哈希、不同路径：视为重复副本，不误判为新版本。

## 5. 确定性契约（本版重点）

- 确定性范围：`sources`、`library`（含 summary/directory_tree/duplicate_groups/version_families）、`answers[].evidence`、`uncertainty`、`pending_human`、`output_contract` 回填。
- 非确定性字段仅限：`change_log[].time`、`runtime.started/finished/pid/python`、`inventory.generated_at`、`state.phase_seconds/_started`。复核 diff 前屏蔽这些键。
- 实现手段：`os.walk` 后对目录与文件名排序；重复组/版本族按 sha256 或基名排序；`write_json` 用 `sort_keys=True` 与固定分隔符；随机性一律禁用。
- `selftest.py` 将整条流水线在临时目录重跑两次，对 scrubbed 结果做全等比较；不等即失败。

## 6. 复核人复跑步骤

1. 安装：把包放到 `/skills/weekly-skill`，确认 `scripts/*.py` 可执行（`python3 scripts/main.py --help` 退出码 0）。
2. 运行：`python3 scripts/main.py --phase all --input /input --output /app/output --work /app/work`。
3. 自检：`python3 scripts/selftest.py --input /input --output /app/output --work /app/work`，退出码须为 0。
4. 核对：打开 `/app/output/result.json`，确认 case_id、status、sources、uncertainty、change_log 与 output_contract 全部键齐全且 JSON 合法。
5. 回链：任取 `answers[].evidence[]`，按 locator 打开原文件确认 quote 逐字命中；对不上的记录视为错误并修正。
6. 复查：空输入目录、坏 JSON、缺 pdftotext 三种情况各跑一次，确认降级产物合法且不崩溃。

## 7. 与参考打法的差异取舍

- 不把“完整、正确”与“可复现”拆成两个目标：以**确定性脚本做事实底座**，模型只负责需语义判断的答案层，二者在 finalize 阶段按固定 schema 合并，避免人工写盘顺序引入漂移。
- 用 `selftest.py` 把“同一输入两次产物一致”从口号变成可执行断言；契约回填与证据逐字命中同样被程序化检查，而不是靠复核人目测。
- 保留六维度完整性但去掉逐文件冗余副本：`sources` 仅存清单级事实，正文证据回到 `evidence[]`，兼顾可核验与体积。
