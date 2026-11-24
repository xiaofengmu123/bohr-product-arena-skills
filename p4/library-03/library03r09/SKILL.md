---
name: library-organizer
description: 把混杂科研资料目录（期刊论文 PDF、预印本、会议论文、学位论文、补充材料、数据集、扫描件、笔记等）盘点、分类、补元数据、规范命名、去重并区分版本，产出可复核、可回滚的清单与操作记录。当需要整理文献/资料库、生成可检索清单或知识结构、维护元数据与版本关系时使用。输入 /input/case.json + task.md，最终产物写到 /app/output/result.json。
version: 1.0.0
---

# library-organizer：科研资料整理（分类·命名·去重·版本，精简可读版）

## 1. 角色与何时使用

本 Skill 把「科研资料整理」做成可复现流程，而非聊天建议。适用：给定一批混杂文献/资料文件，需 (1) 盘点文件、目录与权限；(2) 按规则归入目录层级、补元数据、维护版本关系；(3) 输出可检索清单并实际落盘整理后的目录；(4) 记录跳过/冲突/无权限/重复/恢复。**不适用**：单篇精读、全文翻译、写作润色、文献检索发现。

核心原则：**先落盘、后精修；不编造、可回滚**。每个语义字段（作者/年份/DOI/类别/标签）必须能指回原文片段或元数据；缺失写 `not_reported`，材料不足写 `insufficient_evidence`。

## 2. 输入契约

- 运行目录 = Skill 根目录（平台装在 `/skills/weekly-skill`，脚本一律用相对路径 `scripts/main.py`）。
- 输入：`/input/case.json`（含 `case_id`，可能含 `output_contract`）+ `/input/task.md` + 材料文件/子目录。
- 工作区 `/app/work`（中间产物），最终产物 `/app/output/`。
- 先发现再假设：扫描输入目录，不假设固定文件名/键名；`case.json`、材料目录、`notes.md`、`.bib`/`.ris` 都要识别。
- 文本按 `utf-8-sig → utf-8 → latin-1` 容错读取；单文件读取上限 2MB，超限截断并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 合法 JSON）。字段 = `case_id` + 题目 `output_contract` 全部字段（键名原样保留、缺值给 `null`/`[]`，不改名不删未知键）+ `status` / `sources` / `uncertainty` / `change_log`，另附 `errors` / `degradations` / `pending_human` / `runtime`。

- 默认契约（未提供时）：`library`（catalog/hierarchy/metadata/versions）、`evidence`（hashes/permissions/locators）、`operations`（改名/移动/去重日志）、`failures`（skipped/conflicts/permission_errors/duplicates/recoveries）。
- `status` ∈ `ok | partial | insufficient_evidence | input_error`；判定见 `scripts/main.py`。
- 可编辑产物：`--apply` 时在 `/app/output/library/` 生成实际整理后目录树，并写 `inventory.json`、`organization.json`、`rollback.json` 供复核与回滚。
- 每个关键结论给 `locator + quote`（逐字引用）；冲突两处都记并标 `source_conflict`；缺失写 `not_reported`，绝不虚构 DOI/作者/年份。

## 4. 执行流程（总预算 900 秒、64 次模型请求，单响应 ≤16384 token）

> 硬性要求：**前 3 分钟内先落盘一版 result.json 骨架（status=partial）**，此后只做增量改进，任何中断都不留下空产物。

| 步骤 | 输入 → 输出 | 请求 | 时间 |
|---|---|---|---|
| 0 读题 | 读 case.json+task.md，定 contract/目录维度/命名模板 | 2–3 | ≤60s |
| 1 落骨架 | `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`：立即写 result.json 骨架 + inventory.json | 1 | ≤60s |
| 2 盘点 | 读 inventory.json：文件/目录/权限/类型/哈希/编码 | 2–3 | ≤90s |
| 3 抽取 | 用 pdf-text-tool/pdftotext 抽 PDF/图片文本，存 `/app/work/text/<sha>.txt`；`.bib/.ris/.json` 直接读 | 1–3 | ≤180s |
| 4 语义 | 从原文/文件名/BibTeX 抽标题/作者/年份/期刊/DOI/arXiv，定类别/标签/版本，写 `/app/work/decisions.json` | 3–8 | ≤240s |
| 5 干跑 | 重跑 main.py（默认干跑）核对 organization.json 的去重/版本/命名 | 1–2 | ≤90s |
| 6 落盘 | 重跑 `--apply` 复制到 `/app/output/library/`，写 rollback.json | 1 | ≤90s |
| 7 自检 | 逐条跑第 5 节清单 + `selftest.py`，补 limitations，定 status | 2–3 | ≤90s |

合计约 13–23 次请求（上限 64）；脚本秒级完成，耗时在文本抽取与语义判断。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] **实际效果**：catalog 覆盖批次全部文件；类型/元数据/目录层级/重复/版本/权限来源六类要素逐一交代。
- [ ] **可核验**：目录归属、标签、命名、去重均可由 `locator+quote` 或 SHA-256 复核。
- [ ] **增益可验证**：同一输入重跑两次，除时间戳外 `catalog`/`operations` 逐字节一致（幂等）。
- [ ] **创新**：命名模板、标签归一、哈希去重、冲突合并有规则（`references/rules.md`）且可复用。
- [ ] **稳定**：`--help` 退出码 0；空目录→`insufficient_evidence`；坏 JSON→`degradations`；缺输入→`input_error`；均退出码 0。
- [ ] **易用**：result.json 合法，含 case_id、契约全字段、status/sources/uncertainty/change_log。
- [ ] **生态**：目录规范、命名规则、字段映射、依赖、示例、边界齐全（SKILL.md + rules.md）。

## 6. 降级策略（诚实标注 limitations）

- 缺 PDF 文本/OCR 失败 → 该文件 `has_text_layer=false`，元数据写 `not_reported`，不猜。
- 缺 `decisions.json` 或无证据字段 → 只用文件名/BibTeX/文本强信号，其余 `not_reported`，记入 `uncertainty` + `pending_human`。
- 缺 pdftotext/OCR → 记 `degradations`，保留清单与哈希，整理仍完成。
- 输入为空/缺失 → `insufficient_evidence` / `input_error`，退出码 0，合法骨架。
- 任何中途异常 → 保留最早骨架，错误写入 `errors`，不冒充完成。

## 7. 最小调用示例（示意，非正式案例）

输入 `/input/`：`paper.pdf`、`preprint.pdf`、`refs.bib`、`data.csv`、`sub/paper_copy.pdf`。

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
    --hierarchy document_type,topic --apply
```

示意输出（**示例**）：`result.json.status="ok"`；`library.catalog` 5 条（1 条 `duplicate_of` 指向 `paper.pdf`）；`library/` 出现 `journal_article/<topic>/` 与 `dataset/`；`rollback.json` 列出本次创建文件。真实案例以实际输入为准。

## 8. 边界

只解决「整理/清单/版本关系」一类任务；不生成预写答案、不宣称基准分数；GitHub 链接等提交信息由参赛者另行填写，不在 Skill 内编造。