---
name: sci-library-organizer
description: 把混杂的科研资料目录（期刊论文 PDF、预印本、会议论文、学位论文、补充材料、数据集、扫描件、笔记等）盘点、分类、补元数据、规范命名、去重并区分版本，产出可复核、可回滚的清单与操作记录。当需要整理文献/资料库、生成可检索清单或知识结构、维护元数据与版本关系时使用。输入 /input/case.json + task.md，输出 /app/output/result.json。
version: 2.0.0
---

# sci-library-organizer：科研资料整理（分类·命名·去重·版本，可复核可回滚）

## 1. 角色与何时使用

本 Skill 把「科研资料整理」做成可复现、可复核、可回滚的流程，而不是聊天建议。
适合：给定一批混杂文献/资料文件，需要 (1) 盘点文件、目录与权限；(2) 按题目规则归入目录层级并维护元数据与版本关系；(3) 输出可检索、可交接的清单；(4) 记录跳过/失败/冲突/重复/恢复。**不适用**：单篇精读、全文翻译、写作润色、文献检索发现。

核心原则：**先落盘、后精修；不编造、可回滚**。所有语义字段（作者/年份/DOI/类别/标签）必须能指回原文片段或元数据；证据缺失写 `not_reported`，材料不足写 `insufficient_evidence`，不虚构任何 DOI/作者/年份/结论。

本版设计取舍（相对通用做法）：**把「不确定性」当一等公民**——每个字段带 `field_basis`（`explicit|inferred|not_reported`），顶层 `uncertainty.counts` 量化无文本层/推断分类/缺失字段数，顶层 `limitations` 显式列出全部降级与边界，使“缺数据不编造”可被独立检查。

## 2. 输入契约

- 运行目录：Skill 根目录（平台安装在 `/skills/weekly-skill`，脚本用相对路径 `scripts/main.py`）。
- 输入：`/input/case.json`（含 `case_id`，可能含 `output_contract` 与案例规则）+ `/input/task.md` + 材料文件/子目录。
- 工作区：`/app/work`（中间产物）；最终产物：`/app/output/`。
- 先发现再假设：扫描输入目录，不假设固定文件名/键名；`case.json`、`task.md`、材料目录、`notes.md`、`.bib/.ris` 都要识别。
- 文本按 `utf-8-sig`→`utf-8`→`latin-1` 容错读取；单文件文本上限 2MB，超限截断并记录。
- `case.json` 存在但无法解析时，降级为默认契约并写入 `degradations`，绝不崩溃。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON），字段 = **`case_id` + 题目 `output_contract` 的全部字段 + `status`/`sources`/`uncertainty`/`limitations`/`change_log`**，另附 `errors`/`degradations`/`pending_human`/`runtime`。

- 读取 `case.json.output_contract`（或 `outputContract`/`output_schema`/`result_contract`/`contract`）；键名原样保留，缺失给 `null`/`[]`，不改名、不删未知键。
- 默认契约四键：`library`（catalog/hierarchy/metadata/versions）、`evidence`（hashes/permissions/locators）、`operations`（改名/移动/去重/归档日志）、`failures`（skipped/conflicts/permission_errors/duplicates/recoveries）。
- `status` ∈ `ok | partial | insufficient_evidence | input_error`；判定规则见 `references/rules.md` 第 9 节。
- `sources` 每项带 `source/sha256/locator/quotes`；`uncertainty` 带计数与说明；`limitations` 逐条列出边界；`change_log` 带时间戳增量事件。
- 可编辑产物：`/app/output/library/` 是 `--apply` 后实际整理目录树；`work/inventory.json`、`work/organization.json`、`work/rollback.json` 供复核与回滚。
- 每个关键结论/数值必须有 `locator + quote`；两处冲突两处都记录并标 `source_conflict`；缺失写 `not_reported` 或 `pending_human`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

> 硬性时间要求：**前 3 分钟内必须落盘一版 result.json 骨架**（status=partial）；此后只做增量改进，任何中断都不留下空产物。

| 步骤 | 做什么（输入→输出） | 请求预算 | 时间预算 |
|---|---|---|---|
| 0 读题 | 读 `case.json`+`task.md`，确定 `output_contract`、目录维度、命名模板 | 2–3 | ≤60s |
| 1 落骨架 | `python3 -B scripts/main.py --input /input --output /app/output --work /app/work --hierarchy <dims>`：**立即写 result.json 骨架** + 盘点 | 1 | ≤60s |
| 2 盘点 | 读 `work/inventory.json`：文件、目录、权限、类型、哈希、编码 | 2–3 | ≤90s |
| 3 抽取文本 | 平台 `pdftotext`/`pdfinfo` 抽取 PDF 文本/元数据，文本文件直读，存 `work/text/`；空文本⇒扫描件无文本层 | 1–3 | ≤180s |
| 4 语义判断 | 从原文/BibTeX/RIS/PDF 元数据抽标题/作者/年份/期刊/DOI/arXiv，判定类别、主题、标签、版本，写 `work/decisions.json`（每字段带 locator+quote） | 3–8 | ≤240s |
| 5 干跑核对 | 重跑 `main.py`（默认干跑）→ 读 `organization.json` 计划，核对去重/版本组/命名冲突 | 1–2 | ≤90s |
| 6 实际落盘 | `python3 -B scripts/main.py ... --apply --decisions /app/work/decisions.json` → 复制到 `/app/output/library/`，写 `rollback.json`，刷新 result.json | 1 | ≤90s |
| 7 自检收尾 | 跑 `scripts/selftest.py` + 第 5 节清单，补 limitations，定最终 status | 2–3 | ≤90s |

合计约 13–23 次请求，低于 64 次上限；脚本秒级完成，耗时主要在文本抽取与语义判断。单次 `main.py`（不带 `--decisions`）即可完成全流程；`--decisions` 用于 Agent 后续提供带证据的人工核验。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work [--task /input/task.md] [--hierarchy document_type,topic] [--decisions /app/work/decisions.json] [--apply]
python3 -B scripts/selftest.py    # 自检：接口/空目录/坏JSON/幂等/结构
```

- `--hierarchy`：目录层级维度（`document_type/topic/year/file_type/status` 任意组合，逗号分隔，默认 `document_type,topic`），按 task.md 规则调整。
- `--apply` 才复制文件；默认干跑，绝不修改 `/input`。重复运行幂等：同名不同内容自动 `-N` 后缀，不覆盖。
- 依赖：仅 Python 3 标准库（≥3.8）；无需 pip 安装。`decisions.json` 结构见 `references/rules.md`。

## 6. 自检清单（对应评分维度，逐条可勾选）

- [ ] 实际效果·完整性：`library.catalog` 覆盖输入批次**全部文件**，六类要素（文件类型/元数据字段/目录层级/重复文件/版本差异/权限与来源）逐一交代。
- [ ] 实际效果·正确性：目录归属、标签、文件名、去重结论均可由 `evidence.locator+quote` 或 `sha256` 核对，缺失字段 `not_reported`。
- [ ] 实际效果·可核验性：`sources`/`evidence.hashes`/`rollback.json` 支持独立复跑与回滚。
- [ ] 使用增益·效率：同一输入重跑两次，`catalog`/`operations`/`failures`（除时间戳外）逐字节一致。
- [ ] 创新增益·可复用：命名模板、标签归一、哈希去重、版本聚合规则在 `references/rules.md`，非硬编码。
- [ ] 可靠体验·稳定：`--help` 退出码 0；空目录→`insufficient_evidence`；坏 JSON→`degradations`；缺输入→`input_error`；均不抛异常。
- [ ] 可靠体验·易用：`result.json` 合法 JSON，含 case_id、契约全字段、status/sources/uncertainty/limitations/change_log。
- [ ] 生态贡献·文档：目录规范、命名规则、字段映射、依赖、边界、示例齐全（`references/rules.md`、`references/checklist.md`）。

## 7. 降级策略（诚实标注 limitations，缺数据不编造）

- 缺 PDF 文本 / 加密 PDF / 扫描件无文本层 → `has_text_layer=false`，语义元数据只取 PDF 内嵌元数据或 BibTeX，其余 `not_reported`，不猜。
- 缺 `decisions.json` 或字段无证据 → 脚本只用文件名/文本/BibTeX/PDF 元数据强信号，其余 `unknown`/`null`，写入 `uncertainty` 与 `pending_human`，字段不采纳。
- 缺软件（`pdftotext`/`pdfinfo` 不可用）→ 记录 `degradations`，保留清单与哈希，整理仍可完成，`has_text_layer=None`。
- 元数据不足以命名 → 保留清洗后的原文件名（`naming_mode=original_basename`），不生成伪命名。
- 输入为空/缺失 → 分别 `insufficient_evidence`/`input_error`，退出码 0，产出合法骨架。
- 任何中途异常 → 保留最早骨架；错误写入 `errors`，不冒充完成。

## 8. 最小调用示例（示意，非正式案例）

输入 `/input/`：`paper.pdf`、`preprint.pdf`、`refs.bib`、`data.csv`、`sub/paper_copy.pdf`。

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work --hierarchy document_type,topic --apply
```

示意输出（节选，**标记为示例**）：`result.json.status="ok"`；`library.catalog` 含 5 条（1 条 `duplicate_of` 指向 `paper.pdf`）；`library/` 下出现 `journal_article/...`、`preprint/...`、`dataset/...`；`rollback.json` 列出本次创建文件。真实案例以实际输入为准。

## 9. 边界

只解决「整理/清单/版本关系」一类任务；不生成预写答案、不宣称基准分数、不编造 GitHub 链接或软件状态。正式测试案例、白板与评测结果由平台提供或生成。
