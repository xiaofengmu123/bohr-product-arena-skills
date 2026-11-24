---
name: research-kb-qa
description: 科研知识库资料管理与带来源引用的问答 Skill。以结构化中间表示（plan/inventory/evidence_index/answers/trace）固化“盘点→索引→逐问逐字引用→可复核交付”的可复跑流程；对多文件、跨格式、含重复副本与历史版本的科研文件实现六维度盘点、版本/重复判定、PDF 页索引、证据回链与不确定性分级。当需要整理科研资料、维护版本关系、做带来源引用问答并产出 result.json 时使用。
version: 1.1.0
---

# 科研知识库资料管理与带来源引用的问答

## 1. 角色与何时使用

本 Skill 把“盘点资料 → 六维度索引 → 带来源引用的问答 → 可复核交付”固化为可复跑流程，
解决“回答通顺但引用对不上文件/段落/版本”的问题。适用范围：跨目录、跨格式、含重复副本
与历史版本的科研文件集合，需要产出文件清单、元数据、版本关系、哈希证据与带来源引用的
问答结果，并可被他人按记录重放核对。不适用：无材料的一次性聊天问答；已有专用工具链且
无需复核的场景。

核心设计：每一步的判定依据都沉淀为结构化中间产物（`plan.json` / `inventory.json` /
`evidence_index.json` / `answers.json` / `trace.json`），`finalize` 只做合并不回判，
使任何结论都能沿哈希链回到原文件（详见 `references/design.md`）。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（任务、约束，可能含 `output_contract`、
  `questions`、材料路径）与 `/input/task.md`（题目说明）。
- 材料位于 `/input` 下或 `case.json` 指明的子目录。必须读实际文件，不得依据文件名
  标签臆测内容。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`。
- 脚本仅依赖 Python 标准库，用相对路径调用：`scripts/main.py`（入口）、
  `scripts/core_impl.py`（实现）、`scripts/selftest.py`（自检）。
- 以 `python3` 调用；无 `python3` 时退回 `python`。缺 `pdftotext` 时自动降级并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：取自 `case.json` 的 `case_id`/`id`/`challenge_id`。
- `output_contract` 的全部字段：**原样回填全部键**（不变名、不删未知键、不改值），
  并在顶层逐键拍平；缺值的键填 `null` / `[]` / `{}`。若 `case.json` 没有该对象，
  记入 `degradations` 并按可用字段推导最小契约。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实反映完成度。
- `sources`：文件与来源清单（相对路径、SHA256、类型、大小、来源）。
- `library`：六维度清单摘要（文件类型、元数据字段、目录层级、重复文件、版本差异、
  权限与来源）。`uncertainty`：逐条不确定性分级与理由。`pending_human`：需人工介入项。
- `change_log`：每步操作记录（步骤、时间、动作、状态）。另附 `answers`、`errors`、
  `degradations`、`runtime`、`deliverables`。
- 附带可读产物：`inventory.md`（清单）、`evidence_map.json`（证据映射）。

## 4. 执行流程与预算

总预算：**900 秒、64 次模型请求**。硬规则：**前 3 分钟内先跑 `scaffold` 落盘一版合法
result.json 骨架，之后只做增量改进**；任何一步失败不得崩溃、不得编造数据。

| 阶段 | 时间 | 请求 | 步骤（输入 → 输出） |
|---|---|---|---|
| 0 骨架 | 0–3 min | ~5 | 读 `/input/case.json`、`/input/task.md`；`python3 scripts/main.py scaffold` → `/app/output/result.json` + `/app/work/plan.json`、`case_summary.json` |
| 1 盘点 | 3–12 min | ~20 | `python3 scripts/main.py inventory`；读 `inventory.json`、`evidence_index.json`；抽查关键文件 |
| 2 问答 | 12–14 min | ~15 | 逐问检索 → 摘录逐字证据 → 写 `/app/work/answers.json` |
| 3 汇总 | 14–15 min | 剩余 | `python3 scripts/main.py finalize --answers /app/work/answers.json`；按自检清单补漏 |

`answers.json` 结构见 `references/design.md` 第 3 节：每个答案带 `evidence[]`
（`file`/`sha256`/`locator`/`quote`/`confidence`）与 `uncertainty`、`missing`。

## 5. 问答与证据纪律（写出 answers.json 的硬规则）

阶段 2 是唯一需要模型“生产内容”的步骤，也是质量增益的主要来源。逐问执行以下五步：

1. **检索**：先读 `/app/work/evidence_index.json` 与 `inventory.json`，用 `preview`/`pages`
   定位候选文件；再打开原文件确认精确片段。不得仅凭文件名或类型标签作答。
2. **逐字摘录**：`quote` 必须是原文可定位的连续子串（逐字），禁止改写、省略限定词或
   补造数值。文本用 `locator="<相对路径>#L起-L止"`，PDF 用 `<相对路径>#p页`；`sha256` 必须与
   `inventory.json` 中该文件一致。
3. **一句一证**：`answer` 里的每个关键论断对应至少一条 `evidence`；`certain` 只用于
   逐字+单一来源，摘要或推断标 `likely`。
4. **缺证敢说没有**：库内找不到依据时 `answer` 写“知识库内无依据”，`uncertainty=insufficient_evidence`，
   `missing` 写清缺什么（如“缺 2023 年方法章节”），并进入 `pending_human`。
5. **冲突不隐藏**：同一指标两个来源不一致时全部记录，`uncertainty=conflict`，
   `sources` 列出双方路径，不擅自取舍。

版本规则：引用必须落到具体版本成员（路径+哈希），不得只给版本基名；同一基名、同一
哈希、不同路径视为副本而非新版本。每写完一问答，用
`python3 scripts/main.py finalize --answers /app/work/answers.json` 落盘一次增量结果，
避免超时丢失。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] **完整性**：`library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项。
- [ ] **正确性**：每条答案引用真实、`locator` 定位准确、`quote` 逐字对应，版本与权限处置正确。
- [ ] **可核验性**：`sources`/evidence 含哈希 + 定位，可沿中间表示回到原文件核对。
- [ ] **不确定性**：证据不足/冲突/不可读/权限受限均分级提示并写清缺什么。
- [ ] **可复现**：同一输入两次运行结果一致（固定排序、固定哈希、LF 换行；时间戳仅入 runtime/change_log）。
- [ ] **失败记录**：跳过/冲突/无权限/重复/恢复写入 `degradations` 与 `trace.json`，未完成不写完成。
- [ ] **契约回填**：result.json 含 `case_id` 与 `output_contract` 全部字段，JSON 合法。

交付前运行 `python3 scripts/selftest.py`，须打印 `ALL PASS` 且退出码 0。

## 7. 降级策略（如实标注 limitations，不编造）

- 空输入目录：`status=insufficient_evidence`，退出码 0，写合法结果并列出缺口。
- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，继续处理其余材料；case.json 坏则 `input_error`。
- 缺 `pdftotext`/OCR：PDF 无文本层标 `unreadable`/`needs_ocr`，不编造文本。
- 超 2MB 单文件：跳过文本读取并在 `degradations` 记录，哈希仍计算。
- 写盘失败：降级为最小合法产物（至少 `case_id`、`status`、`sources`、`uncertainty`、`change_log`），不崩溃。
- 超时/超请求：先落盘部分结果，`status=partial` 并记录未完成项到 `pending_human`。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
# Agent 按第 5 节（问答与证据纪律）逐问检索并写 /app/work/answers.json
python3 scripts/main.py finalize --input /input --output /app/output --work /app/work --answers /app/work/answers.json
```

六维度规则与证据定位规范见 `references/checklist.md`；中间表示设计见 `references/design.md`。