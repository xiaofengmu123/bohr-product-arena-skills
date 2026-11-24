---
name: sci-library-planner
description: 把混杂的科研资料目录（期刊论文 PDF、预印本、会议论文、学位论文、补充材料、数据集、扫描件、笔记等）盘点、分类、补元数据、规范命名、去重并区分版本；以“事实层→决策层→计划(plan.json)→执行层”四层中间表示驱动，产出可复核、可回滚、可续跑的结果。输入 /input/case.json + /input/task.md，输出 /app/output/result.json。当需要整理文献/资料库、生成可检索清单或维护元数据与版本关系时使用。
version: 1.0.0
---

# sci-library-planner：科研资料整理（分类·命名·去重·版本，中间表示可复核）

## 1. 角色与何时使用

本 Skill 把“科研资料整理”做成可复现、可审计的流程，而非聊天建议。适用：给定一批混杂文献/资料文件，需要 (1) 盘点文件、目录与权限；(2) 按题目规则归入目录层级并维护元数据与版本关系；(3) 输出可检索、可交接的清单与操作记录；(4) 记录跳过/冲突/无权限/重复/恢复。**不适用**：单篇精读、全文翻译、写作润色、文献检索发现。

核心原则：**先落盘、后精修；不编造、可回滚；先出计划、再动文件**。所有语义字段（作者/年份/DOI/类别/标签）必须能指回原文片段或元数据；证据缺失写 `not_reported`，材料不足写 `insufficient_evidence`。

## 2. 输入契约

- 运行目录：Skill 根目录（平台安装于 `/skills/weekly-skill`，脚本用相对路径 `scripts/main.py`）。
- 输入：`/input/case.json`（含 `case_id`，可能含 `output_contract` 与案例规则）+ `/input/task.md` + 材料文件/子目录。
- 工作区 `/app/work`（中间产物）；最终产物 `/app/output/`（含 `result.json` 与 `library/`）。
- **先发现再假设**：扫描输入目录，不假设固定文件名/键名；`case.json`、`materials/`、`notes.md`、`.bib`、`.ris` 等都识别；`case.json`/`task.md` 视作输入规格，不进入资料目录。
- 文本按 `utf-8-sig`→`utf-8`→`latin-1`→替换容错读取；单文件文本读取上限 2MB，超限截断并记录。
- `case.json` 无法解析时，降级为默认契约并写入 `degradations`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，合法 JSON）：**`case_id` + 题目 `output_contract` 的全部字段 + `status`/`sources`/`uncertainty`/`change_log`**，另附 `errors`/`degradations`/`pending_human`/`runtime`。

- 读取 `case.json.output_contract`（或 `outputContract`/`output_schema`）作为契约键；键名**原样保留**，缺失给 `null`/`[]`，不改名、不删未知键。
- 未提供契约时使用默认契约：`library`（资料结果：catalog/hierarchy/metadata/versions）、`evidence`（哈希/权限/定位）、`operations`（改名/复制/去重日志）、`failures`（skipped/conflicts/permission_errors/duplicates/recoveries）。
- `status` ∈ `ok | partial | insufficient_evidence | input_error`；判定规则见 `scripts/main.py`。
- `sources`：每项带 `source`/`sha256`/`locator`；`uncertainty`：未分类/缺字段/无文本层计数；`change_log`：带时间戳的增量事件。
- 可编辑产物：`/app/output/library/` 是实际整理后的目录树（`--apply` 后生成）；`/app/work/` 保留四层中间产物 `inventory.json`、`decisions.json`、`plan.json`、`rollback.json`。
- 每个关键结论/数值/图元必须有 `locator + quote`；冲突信息两处都记录并标 `source_conflict`；缺失写 `not_reported` 或 `pending_human`，绝不虚构 DOI/作者/年份。

## 4. 中间表示管线（本版核心取舍）

区别于“一步生成结果”，本 Skill 以**四层结构化中间产物**驱动，任何一层都可单独复核、单独重放：

| 层 | 产物 | 输入→输出 | 复核点 |
|---|---|---|---|
| 事实层 | `work/inventory.json` | 扫描 `/input` → 路径/字节/sha256/权限/格式/编码/文本层 | 逐字节可复现，无推断 |
| 决策层 | `work/decisions.json` | facts + BibTeX/RIS/PDF 文本 → 元数据/类型/标签/版本角色（每字段带 evidence） | 断言可指回原文 |
| 计划层 | `work/plan.json` | decisions → 类别路径/新名/动作/去重/版本组的声明式变换 | **落地前可审计** |
| 执行层 | `work/rollback.json` | `--apply` 重放 plan → 复制到 `library/`，记录创建清单 | 删除即可回滚 |

`plan.json` 是 facts+decisions 的纯函数；同一输入两次运行 plan 逐字节一致。`--apply` 只是重放 plan，`/input` 永不修改，同名不同内容以 `-N` 后缀解决、不覆盖。

## 5. 执行流程（总预算 900 秒、64 次模型请求）

> 硬性时间要求：**前 3 分钟内必须先落盘一版 result.json 骨架**；此后只做增量改进，任何中断都不应留下空产物。

| 步骤 | 做什么（输入→输出） | 请求预算 | 时间预算 |
|---|---|---|---|
| 0 读题 | 读 `case.json`+`task.md`，确定 `output_contract`、目录维度、命名模板 | 2–3 | ≤60s |
| 1 落骨架 | `python3 -B scripts/main.py --input /input --output /app/output --work /app/work --hierarchy <dims>`：**立即写 result.json 骨架** + `work/inventory.json` | 1 | ≤60s |
| 2 盘点 | 读 `inventory.json`：文件、目录、权限、类型、哈希、编码 | 2–3 | ≤90s |
| 3 抽取文本 | 用 pdf-text-tool/pdftotext/pdfinfo 抽取 PDF/Office 文本，存 `work/text`；空文本⇒扫描件无文本层；`.bib/.ris/.json` 已含文本 | 1–3 | ≤180s |
| 4 语义判断 | 从原文抽取标题/作者/年份/期刊/DOI/arXiv，判定类别、主题、标签、版本，写 `work/decisions.json`（每字段带 locator+quote） | 3–8 | ≤240s |
| 5 干跑核对 | 重跑 `main.py`（默认干跑）→ 读 `work/plan.json`，核对去重/版本组/命名冲突 | 1–2 | ≤90s |
| 6 实际落盘 | 重跑 `main.py --apply` → 复制保留件到 `/app/output/library/`，写 `rollback.json`，刷新 result.json | 1 | ≤90s |
| 7 自检收尾 | 逐条跑第 7 节清单，补 limitations，定最终 status | 2–3 | ≤90s |

合计约 13–23 次请求、远低于 64 次上限。脚本本身秒级完成，耗时主要在文本抽取与语义判断。

## 6. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
    [--task /input/task.md] [--decisions /app/work/decisions.json] \
    [--hierarchy document_type,topic] [--apply]
python3 -B scripts/selftest.py      # 自检脚本（临时目录端到端回归）
```

- `--hierarchy`：目录层级维度（`document_type/topic/year/format` 任意组合，逗号分隔，默认 `document_type,topic`）；按 task.md 规则调整。
- `--decisions`：可选，传入 Agent 手工核验过的 `decisions.json`；缺省时脚本只采 BibTeX/RIS/PDF 内嵌/DOI/arXiv 等强信号，其余 `not_reported`。
- `--apply` 才复制文件；默认干跑，绝不修改 `/input`。重复运行幂等：同名同内容不重复复制，同名不同内容自动 `-N` 后缀。
- 依赖：仅 Python 3 标准库（≥3.8）；pdftotext/pdfinfo 为可选增强，缺失自动降级。`decisions.json` 结构见 `references/rules.md`。

## 7. 自检清单（逐条勾选，对应评分维度）

- [ ] 实际效果：`library.catalog` 覆盖输入批次**全部文件**，六类要素（文件类型/元数据字段/目录层级/重复文件/版本差异/权限与来源）逐一交代（完成度/完整性）。
- [ ] 实际效果：目录归属、标签、文件名、去重结论均可由 `evidence.locator+quote` 或 `sha256` 核对（正确性/可核验性）。
- [ ] 使用增益：同一输入重跑两次，`catalog`/`operations`/`versions`（除时间戳外）逐字节一致（效率/增益可验证）。
- [ ] 创新增益：`plan.json` 中间表示可审计可重放；命名模板、标签归一、哈希去重、冲突合并在 `references/rules.md` 有可复用规则。
- [ ] 可靠体验：`--help` 退出码 0；空目录→`insufficient_evidence`；坏 JSON→`degradations`；缺输入→`input_error`；均不抛异常（异常处理/稳定性）。
- [ ] 可靠体验：`result.json` 合法 JSON，含 case_id、契约全字段、status/sources/uncertainty/change_log（调用易用）。
- [ ] 生态贡献：目录规范、命名规则、字段映射、依赖、边界、示例齐全（文档完整性/复用）。

自动化校验：`python3 -B scripts/selftest.py`（覆盖 help、空/缺输入、坏 JSON、幂等、去重、版本、apply/rollback）。

## 8. 降级策略（诚实标注 limitations）

- 缺 PDF 文本/OCR 失败 → `has_text_layer=false`，元数据写 `not_reported`，不猜。
- 缺 `decisions.json` 或字段无证据 → 脚本只用文件名/文本/BibTeX 强信号，其余 `unknown`/`null`，写入 `uncertainty` 与 `pending_human`。
- 缺软件（pdftotext/pdfinfo 不可用）→ 记录到 `degradations`，保留清单与哈希，整理仍可完成。
- 输入为空/缺失 → 分别 `insufficient_evidence`/`input_error`，退出码 0，产出合法骨架。
- 任何中途异常 → 保留最早骨架；错误写入 `errors`，不冒充完成。

## 9. 最小调用示例（示意，非正式案例）

输入 `/input/`：`paper.pdf`、`preprint.pdf`、`refs.bib`、`data.csv`、`sub/paper_copy.pdf`。

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
    --hierarchy document_type,topic --apply
```

示意输出（节选，**标记为示例**）：`result.json.status="ok"`；`library.catalog` 含 5 条（1 条 `duplicate_of` 指向 `paper.pdf`）；`work/plan.json` 是落地前可审计计划；`library/` 下出现 `journal_article/...` 与 `dataset/...`；`rollback.json` 列出本次创建文件。真实案例以实际输入为准。

## 10. 边界

只解决“整理/清单/版本关系”一类任务；不生成预写答案、不宣称基准分数。GitHub 链接等提交信息由参赛者另行填写，不在本 Skill 内编造。
