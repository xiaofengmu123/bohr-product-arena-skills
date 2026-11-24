---
name: sci-library-curator
description: 把混杂科研资料目录（期刊论文、预印本、会议论文、学位论文、补充材料、数据集、扫描件、笔记等）盘点、分类、补元数据、规范命名、去重并区分版本，输出可复核、可回滚的清单与操作记录；证据逐字溯源，绝不编造作者/年份/DOI。用于整理文献/资料库、生成可检索清单或知识结构、维护元数据与版本关系。
version: 2.0.0
---

# sci-library-curator：科研资料整理（纯逻辑核心 + 证据门控 + 可回滚）

## 1. 角色与何时使用

本 Skill 把「科研资料整理」做成确定性管线，而非聊天建议。适合：给定一批混杂文献/资料文件，需要 (1) 盘点文件、目录与权限；(2) 按题目规则归入目录层级并维护元数据与版本关系；(3) 输出可检索、可交接的清单；(4) 记录跳过/冲突/无权限/重复/恢复。

**不适用**：单篇精读、全文翻译、写作润色、文献检索发现。

核心原则：**先落盘、后精修；不编造、可回滚**。语义字段（作者/年份/DOI/主题/标签）必须能指回原文逐字片段（locator+quote）；证据缺失写 `not_reported`，材料不足写 `insufficient_evidence`，无逐字证据的断言进入 `pending_human`。

## 2. 架构（与常见「inventory+organize」实现的差异）

- **纯逻辑核心 `scripts/core_impl.py`**：全部业务判断（契约回填、分类、去重、版本并查集、命名、状态、语录校核）都是无文件系统副作用的纯函数，输入输出均为 plain 值，可单独确定性单测。
- **I/O 薄层 `scripts/main.py`**：只负责扫描、读文本、写盘、复制、回滚；不包含业务判断。
- **证据门控**：`decisions.json` 的每条断言必须带 `evidence[].quote`，脚本把 quote 与文件可见文本做**逐字子串比对**，命中才采纳；否则 `pending_human`。
- **阶段 checkpoint**：每阶段写 `work/*.json`（`pipeline.json` 记录 stage），重跑幂等、可续核。
- **三态文本层**：`has_text_layer` = `true`（读到文本）/ `false`（预抽取为空，确认无文本层）/ `null`（尚未抽取）。

## 3. 输入契约

- 运行目录：Skill 根目录（平台安装于 `/skills/weekly-skill`，脚本用相对路径 `scripts/main.py`）。
- 输入：`/input/case.json`（含 `case_id`，可能含 `output_contract`/`outputContract`/`output_schema`）+ `/input/task.md` + 材料文件/子目录。
- 工作区：`/app/work`（中间产物）；最终产物：`/app/output/`。
- 先发现再假设：扫描 `/input` 递归目录，不假设固定文件名/键名；`case.json`、材料目录、`notes.md`、`.bib`、`.ris`、归档包都识别。
- 文本按 `utf-8-sig` → `utf-8` → `latin-1` 容错读取；单文本读取上限 2MB，超限截断。
- `case.json` 存在但无法解析时，降级默认契约并记录到 `degradations`，不崩溃。

## 4. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON），字段 = `case_id` + 题目 `output_contract` 全部字段（键名**原样保留**，缺失给 `null`/`[]`）+ `status`/`sources`/`uncertainty`/`change_log`，另附 `errors`/`degradations`/`pending_human`/`runtime`。

- 未提供契约时使用默认契约：`library`（catalog/hierarchy/metadata/versions）、`evidence`（hashes/sources/permissions/operations）、`failures`（skipped/conflicts/permission_errors/duplicates/recoveries）、`operations`（改名/移动/去重日志）。
- `status` ∈ `ok | partial | insufficient_evidence | input_error`。
- `sources` 每项带 `locator`/`path`/`role`；`uncertainty` 记录未分类/缺字段/无文本层/语录未命中计数；`change_log` 为带时间戳的增量事件。
- `--apply` 时把保留件**复制**到 `/app/output/library/`（绝不改 `/input`），并写 `work/rollback.json`、`work/inventory.json`、`work/organization.json` 供复核与回滚。
- 每个关键结论/数值/图元必须有 `locator + quote`；冲突两处都记并标 `source_conflict`；缺失写 `not_reported`，绝不虚构 DOI/作者/年份。

## 5. 执行流程（总预算 900 秒、64 次模型请求）

> 硬性要求：**前 3 分钟内必须落盘一版 result.json 骨架**；此后只做增量改进，任何中断不留下空产物。

| 步骤 | 做什么（输入→输出） | 请求预算 | 时间预算 |
|---|---|---|---|
| 0 读题 | 读 `case.json`+`task.md`，确定 `output_contract`、目录维度、命名模板 | 2–3 | ≤60s |
| 1 落骨架 | `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`：立即写 partial 骨架 + `pipeline.json` | 1 | ≤60s |
| 2 盘点 | 读 `work/inventory.json`：文件/目录/权限/类型/哈希/编码/归档成员 | 2–3 | ≤90s |
| 3 抽取文本 | 用平台 `pdf-text-tool`/`pdftotext`/`ocr` 抽取 PDF/图片文本，写 `/app/work/text/<sha256>.txt`（空文件⇒确认无文本层）；`.bib/.ris/.json/.md/.csv` 由脚本自读 | 1–3 | ≤180s |
| 4 语义判断 | 从原文抽取标题/作者/年份/期刊/DOI/主题/版本角色，写 `work/decisions.json`（每字段带 locator+quote） | 3–8 | ≤240s |
| 5 干跑核对 | 重跑 `main.py`（默认干跑）→ 读 `work/organization.json`，核对去重/版本组/命名冲突/语录命中 | 1–2 | ≤90s |
| 6 实际落盘 | `main.py --apply` → 复制保留件到 `output/library/`，写 `rollback.json`，刷新 result.json | 1 | ≤90s |
| 7 自检收尾 | 跑 `scripts/selftest.py` + 第 7 节清单，补 limitations，定最终 status | 2–3 | ≤90s |

合计约 13–23 次请求。脚本本身秒级完成；耗时主要在第 3、4 步的文本抽取与语义判断。

## 6. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
    [--hierarchy document_type,topic] [--template "{first_author}-{year}-{short_title}"] \
    [--decisions /app/work/decisions.json] [--apply]
python3 -B scripts/selftest.py          # 交付前自检：空目录/坏JSON/缺输入/幂等/apply
```

- `--hierarchy`：目录层级维度（`document_type/topic/year/type` 任意组合，逗号分隔，默认 `document_type,topic`）。
- `--template`：命名模板，可用 `{first_author}/{year}/{short_title}/{title}/{doi}/{doc_type}/{authors}`。
- `--apply` 才复制；默认干跑，绝不改 `/input`。重跑幂等：同名同内容 `already_present`，同名异内容 `-N` 后缀。
- 依赖：仅 Python 3 标准库（≥3.8）。`decisions.json` 结构见 `references/rules.md`。

## 7. 自检清单（逐条勾选，对应评分维度）

- [ ] 实际效果·完整性：`library.catalog` 覆盖输入批次**全部材料文件**，文件类型/元数据字段/目录层级/重复文件/版本差异/权限与来源六类要素逐一交代。
- [ ] 实际效果·可核验性：目录归属、标签、文件名、去重结论均可由 `evidence.locator+quote` 或 `sha256` 核对。
- [ ] 使用增益·可验证：同一输入重跑两次，`catalog`/`operations`（除时间戳外）逐字节一致（幂等）。
- [ ] 创新增益：命名模板、标签归一、哈希去重、语录校核、并查集版本聚合规则可复用（见 `references/rules.md`）。
- [ ] 可靠体验·稳定性：`--help` 退出码 0；空目录→`insufficient_evidence`；坏 JSON→`degradations`；缺输入→`input_error`；均不抛异常。
- [ ] 可靠体验·易用：`result.json` 合法，含 `case_id`、契约全字段、`status/sources/uncertainty/change_log`。
- [ ] 生态贡献：目录规范、命名规则、字段映射、依赖、边界、示例齐全（`SKILL.md` + `references/`）。

## 8. 降级策略（诚实标注 limitations，不编造）

- 缺 PDF 文本/OCR 失败 → `has_text_layer=false`，元数据写 `not_reported`，不猜。
- 缺 `decisions.json` 或字段无证据/quote 未逐字命中 → 脚本只用文件名/BibTeX 强信号，其余 `null`/`not_reported`，写入 `uncertainty` 与 `pending_human`。
- 缺 `pdftotext`/`ocr` 工具 → 记录到 `degradations`，保留清单与哈希，整理仍可完成。
- 输入为空/缺失 → 分别 `insufficient_evidence`/`input_error`，退出码 0，产出合法骨架。
- 任何中途异常 → 保留最早骨架；错误写入 `errors`，不冒充完成。

## 9. 最小调用示例（示意，非正式案例）

输入 `/input/`：`paper.pdf`、`preprint.pdf`、`refs.bib`、`data.csv`、`sub/paper_copy.pdf`。

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
    --hierarchy document_type,topic --apply
```

示意输出（节选，**标记为示例**）：`result.json.status="ok"`；`library.catalog` 含 5 条（`paper_copy.pdf` 的 `duplicate_of` 指向 `paper.pdf`）；`library/` 出现 `journal_article/...`、`dataset/...`；`rollback.json` 列出本次创建文件。真实案例以实际输入为准。

## 10. 边界

只解决「整理/清单/版本关系」一类任务；不生成预写答案、不宣称基准分数。GitHub 链接由参赛者另行填写，不在本 Skill 内编造。
