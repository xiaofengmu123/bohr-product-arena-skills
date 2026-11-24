---
name: sci-library-organizer
description: 把混杂的科研资料目录（期刊论文 PDF、预印本、会议论文、学位论文、补充材料、数据集、扫描件、笔记等）盘点、分类、补元数据、规范命名、去重并区分版本，产出可复核、可回滚的清单与操作记录。当需要整理文献/资料库、生成可检索清单或知识结构、维护元数据与版本关系时使用。输入 /input/case.json + task.md，输出 /app/output/result.json。
version: 2.0.0
---

# sci-library-organizer：科研资料整理（分类·命名·去重·版本，可复核可回滚）

## 1. 角色与何时使用

本 Skill 把「科研资料整理」做成可复现流程，而不是聊天建议。适合：给定一批混杂文献/资料文件，需要 (1) 盘点文件、目录与权限；(2) 按题目规则归入目录层级并维护元数据与版本关系；(3) 输出可检索、可交接的清单；(4) 记录跳过/失败/冲突/重复/恢复。**不适用**：单篇精读、全文翻译、写作润色、文献检索发现。

核心原则：**先落盘、后精修；不编造、可回滚**。所有语义字段（作者/年份/DOI/类别/标签）必须能指回原文片段或元数据；证据缺失写 `not_reported`，材料不足写 `insufficient_evidence`。

## 2. 输入契约

- 运行目录：Skill 根目录（平台装在 `/skills/weekly-skill`，脚本用相对路径 `scripts/main.py`）。
- 输入：`/input/case.json`（含 `case_id`，可能含 `output_contract` 与案例规则）+ `/input/task.md` + 材料文件/子目录。
- 工作区：`/app/work`（中间产物）；最终产物：`/app/output/`。
- 先发现再假设：真实扫描输入目录，不假设固定文件名/键名；`case.json`、材料目录、`notes.md`、`.bib`、`.ris` 都识别。
- 文本按 `utf-8-sig`→`utf-8`→替换容错读取；单文本文件读取上限 2MB，超限截断并记录。
- `case.json` 存在但无法解析 → 降级为默认契约并记录到 `degradations`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，合法 JSON），字段 = **`case_id` + 题目 `output_contract` 的全部字段 + `status` / `sources` / `uncertainty` / `change_log`**，另附 `errors` / `degradations` / `pending_human` / `runtime`。

- 读取 `case.json.output_contract`（或 `outputContract`/`output_schema`/`contract` 等别名）作为契约键；键名**原样保留**，缺失给 `null`/`[]`，不改名、不删未知键。
- 未提供契约时默认：`library`（catalog/hierarchy/metadata/versions）、`evidence`（hashes/permissions/text/locators）、`operations`（改名/移动/去重日志）、`failures`（skipped/permission_errors/duplicates/conflicts/recoveries）。
- `status` ∈ `ok | partial | insufficient_evidence | input_error`。
- `sources`：每项带 `source` + `quotes`（逐字 quote + locator + field）；`uncertainty`：未分类/无文本层/缺字段计数；`change_log`：带时间戳的增量事件。
- `--apply` 后生成 `/app/output/library/` 实际目录树；同时保留 `inventory.json`、`organization.json`、`rollback.json`。
- 每个关键结论/数值/图元必须有 `locator + quote`；冲突两处都记录并标 `source_conflict`；缺失写 `not_reported` 或 `pending_human`，绝不虚构 DOI/作者/年份。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

> **硬性要求：前 3 分钟内必须落盘一版 result.json 骨架**；此后只做增量改进，任何中断都不应留下空产物。脚本启动即在 `1_skeleton` 阶段写入骨架，并把每阶段耗时写回 `runtime.budget`/`runtime.actual`。

| 阶段 | 做什么（输入→输出） | 请求预算 | 时间预算 |
|---|---|---|---|
| 1_skeleton | 读 `case.json`+`task.md`，确定 `output_contract`，**立即写 result.json 骨架** | 2–3 | ≤60s（硬截止 180s） |
| 2_inventory | `scripts/main.py` 盘点：文件/目录/权限/类型/哈希 | 1 | ≤90s |
| 3_text_analysis | 抽取文本与 BibTeX/RIS 元数据；分类/去重/版本/命名 | 3–8 | ≤300s |
| 4_semantic | 回填 library/evidence/sources/uncertainty/pending_human | 1–2 | ≤60s |
| 5_plan / 6_apply | 干跑核对；`--apply` 复制到 `library/` 并写回滚清单 | 1–2 | ≤60s |
| 7_finalize | 自检收尾、定 status、写最终 result.json | 2–3 | ≤60s |

合计约 10–19 次请求，远低于 64 次上限。脚本秒级完成，耗时主要在文本抽取与语义判断（Agent 用 pdf-text-tool/ocr-tool 补充脚本无法抽取的扫描件/加密件）。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
    [--decisions /app/work/decisions.json] [--hierarchy document_type,topic] [--apply]
python3 -B scripts/selftest.py     # 自检：构造合成输入验证接口与稳定性
```

- `--hierarchy`：目录层级维度（`document_type/topic/year/ext/source` 任意组合，逗号分隔，默认 `document_type,topic`）。
- `--apply` 才复制文件；默认干跑，绝不修改 `/input`。重复运行幂等：同名同内容 `already_present`，不同内容自动 `-N` 后缀，不覆盖。
- `decisions.json` 结构见 `references/rules.md`；依赖仅 Python 3 标准库（≥3.8）。

## 6. 证据与诚实性规则

- 字节级重复用 SHA-256；版本组用并查集按 DOI / arXiv / 归一化标题+年份 聚合；`version_role`（preprint/published/supplementary/revision）只在原文出现标记时判，否则 `unknown`。
- 每个断言字段都要 `evidence:[{locator, quote}]`；无证据的字段进 `pending_human`，不采纳。
- 失败如实记录：坏编码、加密/损坏 PDF、扫描件无文本层、中文乱码、同名不同版本、权限不足分别写 `failures` 子表，不写成完成。

## 7. 自检清单（逐条可勾选，对应评分维度）

- [ ] 实际效果：`library.catalog` 覆盖输入批次**全部文件**；文件类型/元数据字段/目录层级/重复文件/版本差异/权限与来源六类要素逐一交代。
- [ ] 实际效果：目录归属、标签、文件名、去重结论均可由 `evidence.locator+quote` 或 `sha256` 核对。
- [ ] 使用增益：同一输入重跑两次，`catalog`（除时间戳外）逐字节一致。
- [ ] 创新增益：命名模板、标签归一、哈希去重、冲突合并有规则且可复用（`references/rules.md`）。
- [ ] 可靠体验：`--help` 退出码 0；空目录→`insufficient_evidence`；坏 JSON→`degradations`；缺输入→`input_error`；均不抛异常。
- [ ] 可靠体验：`result.json` 合法，含 case_id、契约全字段、status/sources/uncertainty/change_log。
- [ ] 生态贡献：目录规范、命名规则、字段映射、依赖、边界、示例齐全（`references/` 文档）。

## 8. 降级策略（诚实标注 limitations）

- 缺 PDF 文本/OCR 失败 → `has_text_layer=false`，元数据写 `not_reported`，不猜；由 Agent 用 pdf-text-tool/ocr-tool 补 `decisions.json`。
- 缺 `decisions.json` 或字段无证据 → 只用文件名/文本/BibTeX 强信号，其余 `unknown`/`null`，写入 `uncertainty` 与 `pending_human`。
- 缺软件（pdftotext/pdf-text-tool/ocr）→ 记录 `degradations`，保留清单与哈希，整理仍完成。
- 输入为空/缺失 → 分别 `insufficient_evidence`/`input_error`，退出码 0，产出合法骨架。
- 任何中途异常 → 保留最早骨架；错误写入 `errors`，不冒充完成。

## 9. 最小调用示例（示意，非正式案例）

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
    --hierarchy document_type,topic --apply
```

示意输出（节选，**标记为示例**）：`result.json.status="partial"`；`library.catalog` 含全部输入文件（重复件标 `duplicate_of`）；`library/` 下出现 `journal_article/...`、`dataset/...`；`rollback.json` 列出本次创建文件。真实案例以实际输入为准。

## 10. 边界

只解决「整理/清单/版本关系」一类任务；不生成预写答案、不宣称基准分数。GitHub 链接由参赛者另行填写于 `GITHUB_URL.txt`，不在本 Skill 内编造。
