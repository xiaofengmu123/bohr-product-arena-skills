---
name: mindmap-skill
description: 把科研材料（论文/综述/实验方案/项目计划/会议记录）整理为可复核、可编辑的思维导图：材料抽取、层级组织、关系判定、证据挂接、确定性 SVG 渲染，并回填 result.json 契约。当需要依据原始材料生成带来源定位的导图时使用。
version: 1.0.0
---

# 思维导图 Skill

## 1. 角色与何时使用

本 Skill 将给定科研材料整理成一份**可打开、可读、可继续编辑、可逐条回溯来源**的思维导图，用于科研写作准备、研究规划与信息整理。适用于：论文/综述结构梳理、实验与研究方案、项目与任务计划、会议纪要与决策、知识体系与概念关系。

何时使用：任务要求“围绕材料生成结构化导图/梳理研究问题、方法、证据、结论、任务与风险”，且 `/input` 中有案例材料时。不适用：一次性脚本、纯聊天问答、无任何材料的场景（此时诚实返回 `insufficient_evidence`）。

核心目标（对应五个考察点）：**抓得准**（核心概念/问题/方法/证据/结论/任务，不要装饰词）、**理得清**（根节点、层级、同级粒度一致）、**连得对**（关系有依据、方向正确）、**证据可追溯**（节点/关系回溯到 locator+逐字 quote）、**实际有用**（脚本保证确定性渲染与契约回填，重复运行结果一致）。

## 2. 输入契约

运行时原始输入：
- `/input/case.json`：案例标识、任务说明、可能的 `output_contract`（或 `contract` / `result_schema` / `output` / `schema`）。
- `/input/task.md`：任务要求、体裁/受众/格式约束。
- `/input` 下其余材料（`materials/`、`.md/.txt/.json/.bib/.tex` 等）；PDF 用平台 `pdf-text-tool` 提取，**读实际文件，不凭文件名/领域标签脑补内容**。

工作目录 `/app/work`（临时草稿），最终产物写入 `/app/output`。脚本默认路径 `--input /input`、`--output /app/output`、`--work /app/work`。

## 3. 输出契约（必须产出 `/app/output/result.json`）

`result.json` 必须包含：
1. `case_id`（来自 case.json，缺失时降级提取）；
2. **题目 `output_contract` 的全部字段**——由 `scripts/mindmap.py init` 自动读取并**原样回填**，缺值填 `null`/`[]`，不改名、不删未知键；
3. 固定字段：`status`、`sources`、`uncertainty`、`change_log`，以及 `root`/`nodes`/`edges`/`evidence_map`/`artifacts`/`pending_human`/`errors`/`degradations`/`runtime`。

主产物（可编辑）：
- `mindmap.json`：机器可编辑的规范结构；
- `mindmap.md`：人类可编辑的 Markdown 大纲（层级 + 关系 + 内联证据）；
- `mindmap.svg`：确定性树形布局的可视化预览，同时可文本编辑，浏览器/Office 可直接打开（即“可预览”）。

`status` 取值：`ok`（材料齐全且无未决项）/ `partial`（部分完成或有问题）/ `insufficient_evidence`（材料不足）/ `input_error`（输入不可解析）。完整字段与证据定义见 `references/schema.md`。

## 4. 执行流程（总预算 900 秒、64 次模型请求；前 3 分钟必须先落盘骨架）

以下命令在 Skill 包根目录（`SKILL.md` 所在目录，即 `/skills/weekly-skill`）执行；脚本默认 `--input/--output/--work` 已指向 `/input`、`/app/output`、`/app/work`，不传参也能运行。

| 步骤 | 动作 | 输入 → 输出 | 预算 |
|---|---|---|---|
| 1 初始化 | `python3 scripts/mindmap.py init --input /input --output /app/output --work /app/work` | 输入目录 → `/app/work/manifest.json` + `/app/output/result.json` 骨架 | 0–3 min，1–2 req |
| 2 读材料 | 读 `manifest.json`、`task.md` 及各原文；PDF 走 pdf-text-tool | 材料 → 逐字事实/引用清单 | 3–15 min |
| 3 起草 | 手写 `/app/work/mindmap_draft.json`（根/节点/关系/证据，schema 见 references/schema.md） | 材料 → 草稿 | 15–25 min，10–20 req |
| 4 渲染 | `python3 scripts/mindmap.py render --mindmap /app/work/mindmap_draft.json --output /app/output --status partial` | 草稿 → `mindmap.json/.md/.svg` + 更新 result.json | 1 req |
| 5 增量改进 | 复核覆盖/关系方向/证据挂接，补 `sources`/`uncertainty`/`pending_human`，改后重 render，末次定 `--status ok|partial` | 复核 → 更完整产物 | 剩余，≤40 req |
| 6 自检 | `python3 scripts/selfcheck.py --output /app/output --strict` | 产物 → 通过/修复清单 | ≤2 req |

关键纪律：
- **前 3 分钟必须完成第 1 步**，先落一份合法 partial 骨架，任何中途异常都不会留下不完整产物。
- 每次 render 都是全量重写产物，确定性布局，重复运行结果一致（无随机性）。
- 证据必须逐字引用；概算/推断标 `kind=summary|inference` 并降 `confidence`。

## 5. 自检清单（逐条可勾选）

- [ ] `result.json` 可解析，含 `case_id`、`status`、`sources`、`uncertainty`、`change_log` 及全部契约字段。
- [ ] `mindmap.json` / `mindmap.md` / `mindmap.svg` 均存在；`mindmap.svg` 可被 XML 解析。
- [ ] 节点都有唯一 `id`、`label`、`type`；有且仅有一个根（其余节点 `parent` 能闭合，无环）。
- [ ] 每个 edge 的 `from`/`to` 指向存在的节点，`relation` 在合法集合内、方向正确。
- [ ] 关键节点（`evidence/result/conclusion`）与每条关系都带 `locator` + 逐字 `quote` 证据。
- [ ] `evidence_map` 能逐条回溯到原始材料位置；无材料中不存在的连接。
- [ ] 空输入/坏 JSON/坏编码/缺软件：`status` 诚实，异常写入 `errors`/`degradations`/`limitations`，进程不崩溃。
- [ ] 未编造：无预写答案、无伪造评测分数、无虚构引用/DOI/数值。

## 6. 降级策略

- **材料缺失/不足** → `status=insufficient_evidence`，缺口写入 `uncertainty`/`pending_human`；**绝不编造**章节或数值。
- **信息冲突** → 两个来源都记录并标注 `source_conflict`。
- **PDF 无法解析 / 依赖工具缺失** → 记录到 `degradations`/`limitations`，只交付已能产出的部分；不虚构软件状态。
- **单文件超 2MB / 扩展名不支持** → 跳过并记录，继续处理其余材料。
- **写盘失败** → 降级为最小合法产物（最小 result.json），不让进程崩溃；失败原因写入 `errors`。
- `runtime` 中的真实耗时/请求数由平台补记；Skill 自身不编造，未取得的数值留 `0`/`null`。

脚本仅依赖 Python 标准库，路径一律相对（`scripts/…`），可由他人独立运行；调用方式见 `references/checklist.md`，数据结构见 `references/schema.md`。
