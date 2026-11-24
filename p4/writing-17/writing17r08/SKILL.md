---
name: mindmap-academic-writing
description: 围绕科研材料生成可追溯、可编辑、可复核的思维导图并产出带证据映射与诚实状态标注的 result.json。用于把论文/综述/实验方案/项目计划/会议纪要/知识体系等材料整理为层级清晰、关系有据、证据可回溯的思维导图时使用。
version: 1.0.0
---

# 学术写作思维导图 Skill

## 1. 角色与何时使用

本 Skill 让 Agent 把真实科研材料（论文、综述、实验方案、项目计划、会议纪要、任务说明等）
整理成一张**可打开、可编辑、可复核**的思维导图，并同时交付证据映射与运行状态记录。
适用于「把材料整理为思维导图」类任务；不适用于一次性闲聊问答或无需溯源的临时笔记。

核心原则：**抓得准、理得清、连得对、证据可追溯**。绝不编造材料中不存在的事实、数值、
引用或连接；材料不足就如实标注，不补造内容。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json`（任务说明与 `output_contract`）和 `/input/task.md`；
  科研材料（PDF/文本等）也在 `/input/` 或其子目录，**以实际文件为准**，不依据领域标签补造。
- 工作目录 `/app/work/`（中间草稿、提取文本）；最终产物写入 `/app/output/`。
- `output_contract` 可能在 case.json 的任意层级，字段名不固定；脚本会递归发现并原样回填。

## 3. 输出契约（/app/output/）

必须产出 `/app/output/result.json`，包含：

- `case_id`：与输入一致；
- **题目 output_contract 的全部字段**：逐键回填到 result.json 顶层，缺值给 `null`，
  不改名、不删除未知键，并在 `output_contract` 中保留完整副本；
- 附加字段：`status`（`ok | partial | insufficient_evidence | input_error`）、
  `sources`、`uncertainty`、`change_log`，以及 `evidence_map`、`errors`、
  `degradations`、`pending_human`、`limitations`、`artifacts`、`runtime`。

主产物（可编辑 + 可预览）：

- `mindmap.md`：可编辑大纲，末尾含「证据映射」「不确定性与缺口」小节；
- `mindmap.json`：规范化节点/关系/证据结构；
- `mindmap.mm`：FreeMind XML，可导入 FreeMind/XMind 继续编辑；
- `mindmap.svg`：矢量预览（浏览器可打开）；若环境有光栅化工具则另附 `mindmap.png`。

## 4. 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 tokens）

**前 3 分钟必须先落盘一版结果骨架**，之后只做增量改进；任何时刻 result.json 都要合法。

| 步骤 | 输入 | 动作 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 落骨架 | /input | 运行 `python3 scripts/main.py --input /input --output /app/output --work /app/work` | result.json 骨架 + /app/work/inventory.json | ≤60s |
| 1 读材料 | inventory.json | 读 /app/work/inventory.json 的提取文本，精读原文关键段（PDF 缺文本时用 pdf 工具补提取） | 事实底稿、受众、格式约束 | 3–5 min |
| 2 抽节点 | 材料原文 | 按 references/rules.md 抽取根/节点/层级/关系，每条带 locator+quote+provenance | /app/work/draft.json | 3–4 min |
| 3 渲染 | draft.json | `python3 scripts/mindmap.py --draft /app/work/draft.json --output /app/output` | mindmap.md/.json/.mm/.svg + 更新 result.json | ≤30s |
| 4 自检 | /app/output | `python3 scripts/selfcheck.py --output /app/output` | PASS/FAIL 清单 | ≤20s |
| 5 迭代 | FAIL 项 | 修订 draft.json 后重复步骤 3–4，直到预算耗尽前保留最新合法版本 | 增量改进的产物 | 剩余时间 |

请求预算分配建议：步骤 1 约 8–12 次，步骤 2 约 15–25 次，步骤 3–5 每次 2–4 次，共 ≤64 次。
每步都把关键结论的 locator+quote 写进 draft.json，避免事后返工。

## 5. 自检清单（对应评分维度，逐条勾选）

运行 `python3 scripts/selfcheck.py --output /app/output`，并人工核对：

- [ ] case_id 与 /input/case.json 一致；result.json 含全部 output_contract 字段。
- [ ] 根节点=主题/任务目标；节点为核心概念/问题/方法/证据/结论/任务/风险，无装饰词。
- [ ] 层级 ≤6 层、同层粒度一致、无交叉与重复（理得清）。
- [ ] 每条关系有方向+类型+依据；无材料外连接；冲突用 `conflict` 并保留双来源（连得对）。
- [ ] 关键节点/关系带 locator+quote；provenance 区分 fact/summary/inference（证据可追溯）。
- [ ] 数字/术语/引用可逐条回溯，未新增材料外事实。
- [ ] 可编辑产物（.md/.json/.mm）+ 可预览产物（.svg 或 .png）齐全。
- [ ] status 与材料充分度一致；limitations/pending_human 覆盖缺口；change_log 真实。

完整对照表见 references/checklist.md。

## 6. 降级策略（缺数据 / 缺软件时如实标注，禁止编造）

| 场景 | 处置 |
|-|-|
| 空输入 / 无材料 | status=`insufficient_evidence`，limitations 写明缺什么，退出码 0，不抛异常 |
| 坏 JSON / 坏编码 | 记入 errors/degradations，继续处理其余材料，不崩溃 |
| 无 pdftotext / 无 PNG 光栅化 | degradations 记录；SVG 仍作为矢量预览交付 |
| 材料不足 / 冲突 | 缺项写 `pending_human`，冲突标 `conflict` 并保留来源，不擅自取舍 |
| 节点规模爆炸 | 层级 ≤6、节点 ≤120，超限按重要性归并并说明依据 |
| 缺 output_contract | 用回退字段 `deliverable/evidence_map/version_boundary`，并注明为回退 |

脚本均为纯 Python 标准库实现，无随机性，重复运行结果一致。若某步骤被服务失败中断，
result.json 保留上次合法骨架并如实记录中断点，不伪造完成状态。

## 7. 最小调用示例（示意，非正式案例）

```bash
# 第一步：发现输入并落骨架（退出码 0）
python3 scripts/main.py --input /input --output /app/output --work /app/work

# 第二步：Agent 读完 /app/work/inventory.json 后写出草稿（示例，非答案）
# /app/work/draft.json 形如：
# {"case_id":"demo","title":"示例主题",
#  "root":{"label":"示例主题","kind":"concept","evidence":[],
#    "children":[{"label":"研究问题","kind":"question","evidence":[]}]},
#  "relations":[],"sources":[],"uncertainty":[],"contract":{}}

# 第三步：渲染产物并更新 result.json
python3 scripts/mindmap.py --draft /app/work/draft.json --output /app/output

# 第四步：自检
python3 scripts/selfcheck.py --output /app/output
```

脚本调用路径一律使用相对路径（scripts/main.py）。Windows 下可把 `python3` 换成 `python`。
