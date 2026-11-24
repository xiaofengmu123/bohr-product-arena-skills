---
name: research-kb-qa
description: 科研知识库资料管理与带来源引用问答 Skill。按阶段预算（900 秒 / 64 次模型请求）盘点 /input 下文件与目录权限，做 SHA256 去重与版本归族，抽取可检索文本并建立证据索引；前 3 分钟先原子落盘一版合法 partial 骨架，之后只做单调增量改进；对多文件问答逐条给出 locator + 逐字 quote 证据回链，并自动核对引用是否逐字命中原文；交付可复核、可复跑的 result.json、inventory.md 与 evidence_map.json。当需要整理科研资料、维护版本关系、做带来源引用问答或产出可交接清单时使用。
version: 1.1.0
---

# 科研知识库资料管理与带来源引用问答

## 1. 角色与何时使用

本 Skill 把“盘点资料 → 组织元数据/版本 → 带来源引用的问答 → 可复核交付”固化为可复跑、可计时的流水线，重点解决“回答通顺但引用对不上文件/段落/版本”与“运行到中途崩溃导致无产物”两个典型失败。适用于：跨目录、跨格式、含重复副本与历史版本的科研文件集合，需要产出文件清单、元数据、版本关系、哈希证据与带来源引用的问答结果。不适用于：无材料的一次性聊天问答；已有专用工具链且无需复核与降级的场景。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（任务、约束，可能含 `output_contract`、`questions`、材料路径）与 `/input/task.md`（题目说明）；材料位于 `/input` 下或其子目录。
- 必须读实际文件，不依据文件名标签臆测内容；PDF 以真实文本层为准，扫描件无文本层时标 `unreadable`/`needs_ocr`，不编造。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`。脚本用相对路径 `scripts/main.py <子命令>` 调用，仅依赖 Python 标准库。
- `case.json` 或 `task.md` 中给出的 `output_contract` 是硬约束：全部键原样回填，不改名、不删除未知键。

## 3. 输出契约

必须产出 `/app/output/result.json`，结构如下（顶层键与 `output_contract` 键并列共存）：

- `case_id`：取自 `case.json` 的 `case_id/id/challenge_id`。
- `output_contract` 的全部字段：**原样回填全部键**；缺值按契约类型给 `null`/`[]`/`{}`，未自动填充的键列入 `pending_human`。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实反映完成度。
- `answers`：逐问答案 + 证据回链（`file`、`sha256`、`locator`、`quote`、`verification`）。
- `library`：六维度清单摘要（文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源）。
- `sources`：文件清单（相对路径、SHA256、类型、大小、编码、权限、版本标记、来源）。
- `uncertainty`：逐条不确定性分级与理由。
- `pending_human`、`errors`、`degradations`、`change_log`、`runtime`、`budget`、`reproducible_hash`。

另附可读产物：`inventory.md`（清单）、`evidence_map.json`（证据映射，供独立复核）。

## 4. 执行流程与阶段预算（硬规则）

总预算 **900 秒、64 次模型请求**。硬规则：**收到输入后的首次模型请求内先运行 `scaffold`，最迟 180 秒原子落盘一版合法 `result.json` 骨架（`status=partial`），之后只做单调增量改进**；任何一步失败不得崩溃、不得编造数据。

| 阶段 | 截止(累计) | 请求预算 | 命令 | 输入 → 输出 |
|---|---|---|---|---|
| 0 骨架 | ≤180s | ~5 | `python3 scripts/main.py scaffold` | `/input` → `/app/output/result.json` + `/app/work/case_summary.json`、`budget.json`、`clock.json` |
| 1 盘点 | ≤720s | ~20 | `python3 scripts/main.py inventory` | `/input` → `/app/work/inventory.json`、`evidence_index.json`、`extracted/` |
| 2 问答 | ≤840s | ~15 | Agent 检索 `extracted/` → 逐字摘录 → 写 `/app/work/answers.json` | 材料+索引 → `/app/work/answers.json` |
| 3 汇总 | ≤900s | 剩余~24 | `python3 scripts/main.py finalize` | answers+inventory → 最终 `/app/output/result.json` + `inventory.md` + `evidence_map.json` |

每阶段用 `scripts/main.py mark --phase <阶段>` 记录一次请求消耗；`finalize` 读取 `/app/work/request_ledger.json` 汇总 `requests_used`，超时/超请求时 `status=partial` 并写 `degradations`。详细协议见 `references/budget-protocol.md`。

`answers.json` 结构：

```json
{
  "answers": [
    {"question": "...", "answer": "...",
     "evidence": [{"file": "materials/paper.md", "sha256": "...", "locator": "materials/paper.md#L12-L15", "quote": "逐字片段", "confidence": "certain"}],
     "uncertainty": "certain", "missing": null}
  ],
  "uncertainty": [{"level": "conflict", "item": "...", "reason": "两来源不一致", "sources": ["a", "b"]}]
}
```

## 5. 证据回链与不确定性（硬规则）

- 每个关键论断必须带 `file`（相对路径）、`sha256`、`locator`（文本 `materials/paper.md#L12-L15`，PDF `#p<页码>`）、`quote`（原文逐字片段）。
- `finalize` 自动核对：文件是否存在、`sha256` 是否一致、`quote` 是否逐字命中原文，写入 `verification`；不一致降级为 `partial` 并记入 `errors`，不隐藏。
- 不得虚构引用、DOI、数值、页码；拿不到写 `not_reported` 或 `pending_human`。
- 不确定性分级：`certain`（逐字+单一来源）、`likely`（推断/摘要）、`conflict`（来源矛盾，全部记录并标 `source_conflict`）、`insufficient_evidence`（库内无依据，写明缺什么）、`unreadable`（不可读/扫描件无文本层/权限不足）。
- 冲突不隐藏：同一指标两来源不一致时两个都记录。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] **完整性**：`library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项。
- [ ] **正确性**：每条 `answers[].evidence[]` 的 `verification` 为 `verified`（`sha_match` 与 `quote_match` 均真），版本与权限处置正确。
- [ ] **可核验性**：`sources` 含 SHA256 与权限，`evidence_map.json` 含 locator+quote，他人可复跑核对。
- [ ] **不确定性**：证据不足/冲突/不可读/权限受限均分级提示并说明缺什么。
- [ ] **可复现**：`reproducible_hash` 稳定（相同输入两次一致），排序确定性，`runtime`/`change_log` 为墙钟元数据、不进入哈希。
- [ ] **早落盘**：`change_log[0]` 时间戳 ≤ 启动后 180s，且其动作等于 `scaffold_skeleton_written`。
- [ ] **失败记录**：跳过/冲突/无权限/重复/恢复写入 `degradations`/`errors`/`failures_recovery`，未完成不写完成。
- [ ] **契约回填**：`result.json` 含 `case_id` 与 `output_contract` 全部键，JSON 合法；未填充键进 `pending_human`。

运行 `python3 scripts/main.py selftest` 可自动验证上述多项（空目录、坏 JSON、正常样例三场景）。

## 7. 降级策略

- 空输入目录 / 无可用材料：`status=insufficient_evidence`，退出码 0，写合法结果并列出缺口。
- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，继续处理其余材料；`case.json` 不可解析时 `status=input_error`。
- 缺 `pdftotext` / OCR 或 PDF 无文本层：标 `unreadable`/`needs_ocr`，不编造文本。
- 写盘失败：降级为最小合法产物 `{"case_id","status":"partial","sources","uncertainty","change_log"}`，不让进程崩溃。
- 超时/超请求/单文件超 2MB：跳过并记录，`status=partial`，在 `runtime`/`budget` 中如实标注 `limitations`。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 scripts/main.py scaffold   # 首次请求内执行，180s 前落盘 partial 骨架
python3 scripts/main.py inventory  # 盘点 + 抽取文本 + 建证据索引
python3 scripts/main.py mark --phase qa
# Agent 按第 4 阶段检索 extracted/ 后写 /app/work/answers.json
python3 scripts/main.py finalize   # 汇总、核对引用、回填契约、定状态
python3 scripts/main.py selftest   # 交付前自检（可单独在空目录/坏 JSON 场景复跑）
```

六维度规则与证据定位规范见 `references/checklist.md`；阶段预算与早落盘协议见 `references/budget-protocol.md`。
