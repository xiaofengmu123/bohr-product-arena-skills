---
name: sci-library-curator
description: 把混杂的科研资料目录（期刊论文 PDF、预印本、会议论文、学位论文、补充材料、数据集、扫描件、笔记等）盘点、分类、补元数据、规范命名、去重并区分版本，产出可复核、可回滚、可复跑的清单与操作记录。当需要整理文献/资料库、生成可检索清单、维护元数据与版本关系时使用。输入 /input/case.json + task.md，输出 /app/output/result.json。
version: 2.0.0
---

# sci-library-curator：科研资料自动整理（分类·命名·去重·版本，确定性与自检优先）

## 1. 角色与何时使用

本 Skill 把「科研资料整理」做成可复现、可独立验证的确定性流程，而不是聊天式建议。适用场景：给定一批混杂文献/资料文件，需要 (1) 盘点文件、目录与权限；(2) 按题目规则归入目录层级并维护元数据/版本关系；(3) 输出可检索、可交接的清单；(4) 记录跳过/冲突/无权限/重复/恢复。**不适用**：单篇精读、全文翻译、写作润色、文献检索发现。

核心原则：**先落盘、后精修；不编造、可回滚；同输入、同产物**。所有语义字段（作者/年份/DOI/类别/标签）必须能指回原文片段或元数据；证据缺失写 `not_reported`，材料不足写 `insufficient_evidence`。本版侧重点：**自检与确定性**——内置 `scripts/selftest.py` 与可勾选清单，保证同一输入两次运行 `catalog/operations/evidence/versions/failures` 逐字节一致。

## 2. 输入契约

- 运行目录：Skill 根目录（平台安装在 `/skills/weekly-skill`；脚本一律用相对路径 `scripts/main.py`）。
- 输入：`/input/case.json`（含 `case_id`，可能含 `output_contract` 与案例规则）+ `/input/task.md` + 材料文件/子目录。
- 工作区：`/app/work`（中间产物）；最终产物：`/app/output/`（含 `result.json`）。
- 先发现再假设：扫描输入目录，不假设固定文件名/键名；`case.json`、材料目录、`notes.md`、`.bib/.ris/.json` 等都要识别。
- 文本按 `utf-8-sig`→`utf-8`→`latin-1` 容错读取；单文件文本读取上限 2MB，超限截断并记录。
- `case.json` 存在但无法解析时，降级为默认契约并写入 `degradations`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，合法 JSON），字段 = **`case_id` + 题目 `output_contract` 的全部字段 + `status / sources / uncertainty / change_log`**，另附 `pending_human / errors / degradations / runtime` 用于健壮性说明。

- 读取 `case.json.output_contract`（或 `outputContract`/`output_schema`/`result_contract`/`contract`）作为契约键；键名**原样保留**，缺失给 `null`/`[]`，不改名、不删未知键。
- 未提供契约时使用默认契约：`library`（catalog/hierarchy/metadata/versions/search_index）、`evidence`（hashes/permissions/locators/sources_map）、`operations`（分类/改名/去重/apply 日志）、`failures`（skipped/conflicts/permission_errors/duplicates/recoveries）。
- `status` ∈ `ok | partial | insufficient_evidence | input_error`；判定规则见 `scripts/core_impl.py`。
- `sources`：每项带 `source`/`sha256`/`locator`/`quotes`；`uncertainty`：缺失字段/未分类/无文本层计数；`change_log`：带时间戳的增量事件。
- 可编辑产物：`/app/output/library/` 是 `--apply` 后实际整理出的目录树；`inventory.json`、`organization.json`、`rollback.json` 同步落到 `output/` 与 `work/` 供复核与回滚。
- 每个关键结论必须有 `locator + quote` 或 `sha256`；冲突信息两处都记录并标 `source_conflict`；缺失写 `not_reported` 或 `pending_human`，绝不虚构 DOI/作者/年份。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

> 硬性要求：**前 3 分钟内必须先落盘一版 result.json 骨架**；之后只做增量改进，任何中断都不应留下空产物。

| 步骤 | 做什么（输入→输出） | 请求预算 | 时间预算 |
|---|---|---|---|
| 0 读题 | 读 `case.json`+`task.md`，确定 `output_contract`、目录维度、命名模板 | 2–3 | ≤60s |
| 1 落骨架 | `python3 -B scripts/main.py --input /input --output /app/output --work /app/work --hierarchy <dims> --skeleton-only`：**立即写 result.json 骨架** | 1 | ≤60s |
| 2 盘点 | 重跑 `main.py`（不带 `--skeleton-only`）→ 读 `work/inventory.json`：文件、目录、权限、类型、哈希、编码 | 2–3 | ≤90s |
| 3 抽取文本 | 用平台 pdf-text-tool/pdftotext/ocr 抽取 PDF/图片文本，存 `/app/work/text/<sha256>.txt`（空文本⇒扫描件无文本层）；`.bib/.ris/.json` 已含文本 | 1–3 | ≤180s |
| 4 语义判断 | 从原文抽取标题/作者/年份/期刊/DOI/arXiv，判定类别、主题、标签、版本，写 `/app/work/decisions.json`（每字段带 locator+quote） | 3–8 | ≤240s |
| 5 干跑核对 | `python3 -B scripts/main.py ... --decisions /app/work/decisions.json` → 读 `output/organization.json`，核对去重/版本组/命名冲突 | 1–2 | ≤90s |
| 6 实际落盘 | 追加 `--apply` → 复制保留件到 `/app/output/library/`，写 `rollback.json`，刷新 result.json | 1 | ≤90s |
| 7 自检收尾 | 跑 `python3 -B scripts/selftest.py`，逐条勾选第 5 节清单，补 limitations，定最终 status | 2–3 | ≤90s |

合计约 13–23 次请求，远低于 64 次上限。脚本本身秒级完成，耗时主要在文本抽取与语义判断。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
    [--task /input/task.md] [--decisions /app/work/decisions.json] \
    [--hierarchy document_type,topic] [--apply] [--skeleton-only]
python3 -B scripts/selftest.py        # 自检：异常场景 + 确定性 + 幂等，退出码 0 表示通过
```

- `--hierarchy`：目录层级维度（`document_type/topic/year/version_role` 任意组合，逗号分隔，默认 `document_type,topic`）；按 task.md 规则调整。
- `--apply` 才复制文件；默认干跑，绝不修改 `/input`。重复运行幂等：同内容目标标 `already_present`，同名不同内容自动 `-N` 后缀，不覆盖。
- 依赖：仅 Python 3 标准库（≥3.8）；无需 pip。`decisions.json` schema 见 `references/rules.md`。脚本结构：`main.py`（CLI 入口）→ `core_impl.py`（整理逻辑）→ `selftest.py`（自检）。

## 6. 证据与诚实性规则

- 字节级重复用 SHA-256 判定；版本组用并查集按 DOI/arXiv/归一化标题+年份聚合（`preprint/published/supplementary/revision` 只在有原文标记时判，否则 `unknown`）。
- `decisions.json` 每个断言字段都要带 `evidence:[{locator, quote}]`；无证据字段进入 `pending_human`，不采纳。
- 失败如实记录：坏编码、加密 PDF、扫描件无文本层、中文乱码、同名不同版本、权限不足分别写入 `failures` 子表，不得写成完成。

## 7. 自检清单（逐条勾选，对应评分维度）

- [ ] 实际效果：`library.catalog` 覆盖输入批次**全部文件**，六类要素（文件类型/元数据字段/目录层级/重复文件/版本差异/权限与来源）逐一交代（完成度/完整性）。
- [ ] 实际效果：目录归属、标签、文件名、去重结论均可由 `evidence.locator+quote` 或 `sha256` 核对（正确性/可核验性）。
- [ ] 使用增益：同一输入重跑两次，`catalog/operations/evidence/versions/failures` 逐字节一致（效率/增益可验证）。
- [ ] 创新增益：命名模板、标签归一、哈希去重、冲突合并有规则且可复用（`references/rules.md`）。
- [ ] 可靠体验：`--help` 退出码 0；空目录→`insufficient_evidence`；坏 JSON→`degradations`；缺输入→`input_error`；均不抛异常（异常处理/稳定性）。
- [ ] 可靠体验：`result.json` 合法，含 case_id、契约全字段、status/sources/uncertainty/change_log（调用易用）。
- [ ] 生态贡献：目录规范、命名规则、字段映射、依赖、边界、示例齐全（文档完整性/复用）。

完整逐条验收清单见 `references/checklist.md`，脚本回归见 `scripts/selftest.py`。

## 8. 降级策略（诚实标注 limitations）

- 缺 PDF 文本/OCR 失败 → `has_text_layer=false`，元数据写 `not_reported`，不猜。
- 缺 `decisions.json` 或字段无证据 → 脚本只用文件名/文本/BibTeX 强信号，其余 `unknown`/`null`，写入 `uncertainty` 与 `pending_human`。
- 缺软件（pdftotext/ocr 不可用）→ 记入 `degradations`，保留清单与哈希，整理仍可完成。
- 输入为空/缺失 → 分别 `insufficient_evidence`/`input_error`，退出码 0，产出合法骨架。
- 任何中途异常 → 保留最早骨架；错误写入 `errors`，不冒充完成。

## 9. 最小调用示例（示意，非正式案例）

输入 `/input/`：`paper.pdf`、`preprint.pdf`、`refs.bib`、`data.csv`、`sub/paper_copy.pdf`。

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
    --hierarchy document_type,topic --apply
```

示意输出（节选，**标记为示例**）：`result.json.status="ok"`；`library.catalog` 含 5 条（1 条 `duplicate_of` 指向 `paper.pdf`）；`library/` 下出现 `journal_article/...` 与 `dataset/...`；`rollback.json` 列出本次创建文件。真实案例以实际输入为准。

## 10. 边界

只解决「整理/清单/版本关系」一类任务；不生成预写答案、不宣称基准分数。GitHub 链接等提交信息由参赛者另行填写，不在本 Skill 内编造。
