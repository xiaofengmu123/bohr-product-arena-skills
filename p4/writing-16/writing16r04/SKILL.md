---
name: sci-writing-transform
description: 科研成果转化写作 Skill。把科研论文、技术报告或项目材料改写成面向指定读者的可编辑稿件，锁定术语与论断强度，逐条保留数值、单位、比较基准、样本量、统计口径、图表编号与引用，产出逐段对照、证据映射和可复核的 result.json。用于论文摘要改科普、技术报告改申报材料、多文体改写与校对等场景。
version: 1.0.0
---

# 科研成果转化写作（Sci Writing Transform）

## 1. 角色与何时使用

本 Skill 把“科研材料 → 指定受众与体裁的可编辑改写稿”沉淀为可复现流程。只解决一类问题：**在不漂移事实、不拔高论断的前提下，完成一次可直接使用的成果转化改写**，而不是给建议、提纲或片段润色。

触发条件（任一满足即可使用）：
- 输入给出源文 + 目标读者 + 体裁/文体，如“论文摘要改为公众科普文章”“技术报告改为项目申报材料”。
- 任务要求改写、校对、格式、引用、双语或结构化产出，且要求保留来源。

不适用：一次性脚本；无真实源文的纯问答。核心目标四项：**读得懂**（不展开缩写/公式不解释/行话挡路）、**留得住**（删冗余而非删证据）、**说得准**（论断强度与证据匹配）、**接得住**（每处删改可逐段复核）。

## 2. 输入契约

运行时原始输入在 `/input/case.json` 与 `/input/task.md`，材料（PDF/文本/表格等）随题提供。**先发现再假设**：
- 扫描 `/input/`，不假设文件名与键名；`case.json`、`task.md`、`materials/`、`notes.md` 等都要识别。
- 文本一律按 `utf-8-sig` 优先读取，坏编码降级到 `gb18030` 再 `replace`，坏 JSON 记录错误并继续处理其余材料。
- 单文件超过 2MB 跳过并记录到 `degradations`。
- 若 `case.json` 含 `output_contract` / `output_schema` 等字段，其键**原样镜像**进 result.json，缺值给 `null`/`[]`，不改名、不删未知键。
- 先读实际文件内容，不依据文件名/标签补造数据。

运行环境：工作目录用 `/app/work`，最终产物写 `/app/output/`。脚本位于 Skill 根目录，用相对路径调用：`python3 -B scripts/main.py …`（在任何 cwd 下均可运行，脚本自行定位同目录依赖）。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段如下（含 `case_id` + 题目 `output_contract` 全部字段 + 下列标准字段）：

| 字段 | 说明 |
|-|-|
| `case_id` | 输入标识，从 case.json 提取；缺省为 `null` 并标 `input_error` |
| `status` | 只能取 `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `artifacts` | 目标产物清单：`[{path, format, description}]`，可编辑稿 + 预览 |
| `evidence_map` | 事实与来源映射：`[{item, locator, quote, kind, strength}]` |
| `change_log` | 逐段对照：`[{locator, before, after, reason, type}]` |
| `pending_human` | 待人工确认项（材料不足/冲突/涉密/未发表） |
| `sources` | 材料来源：`[{path, role}]` |
| `uncertainty` | 不确定性说明（字符串） |
| `limitations` / `errors` / `degradations` | 限制、错误、降级记录 |
| `runtime` | `{started_at, finished_at}` |

主产物要求：文本类给 `.md`；Office/幻灯片/图表类同时给可编辑格式与真实渲染的 PDF/PNG 预览。`status` 诚实：材料不足写 `insufficient_evidence`，不编造数据或结论。

## 4. 执行流程（总预算：900 秒、64 次模型请求、16384 token/回复）

**步骤 0｜勘察与先落盘（≤180 秒，前 3 分钟内必须完成）**
输入：`/input/`。执行 `python3 -B scripts/main.py init`，扫描材料、提取 `case_id` 与 `output_contract`，立即写出 `/app/output/result.json` 骨架（`status=partial`）与 `/app/work/plan.json`。此后任何中断都不留不完整产物。
产出：result.json 骨架、plan.json。

**步骤 1｜受众画像与取舍（约 150 秒）**
读 `task.md`/`case.json` 确定目标读者、体裁、篇幅上限；参照 `references/genre_profiles.md` 建立读者画像；逐项把内容分为必须保留 / 可压缩 / 可删除；锁定术语表（缩写首次展开 + 同义词收敛）。
产出：术语表 + 保留优先级清单（写入 `/app/work/`，可并入 result）。

**步骤 2｜论断强度分级（约 60 秒）**
对关键结论/数值标注 `fact`/`causal`/`correlational`/`preliminary`/`not_reported`（见 `references/checklist.md`），确定哪些结论可简化、哪些必须保留限定语。
产出：论断强度清单，写入 evidence_map 的 `strength` 字段。

**步骤 3｜改写与落稿（约 300 秒，核心）**
按受众与体裁完成整篇可编辑稿；数值/单位/比较基准/样本量/统计口径/图表编号/引用逐条与源文核对；删改实时写入 `change_log`（locator + before + after + reason + type）。
产出：`/app/output/` 下主产物（如 `draft.md` / `draft.docx`）+ 预览。

**步骤 4｜逐段对照与待确认（约 120 秒）**
补齐 `evidence_map`（每项带 locator + 逐字 quote）；把材料冲突、无法判断、涉密/未发表项写入 `pending_human`；确认 `sources`、`uncertainty`、`limitations` 完整。
产出：完整 evidence_map、change_log、pending_human。

**步骤 5｜自检与定稿（约 90 秒，含缓冲）**
执行 `python3 -B scripts/main.py finalize` 校验 schema、写入 finish 时间、按问题数把 `ok` 降为 `partial`；逐条核对 `references/checklist.md`；若材料不足，将 `status` 设为 `insufficient_evidence`。
产出：最终 `/app/output/result.json` 与全部可编辑产物。

请求与 token 管理：把材料读入 `/app/work/` 缓存后不再重复全文读取；每步只做必要请求；前 3 分钟先落盘骨架，之后只做增量改进，不重写已定稿部分。

## 5. 自检清单（提交/交付前逐条勾选）

- [ ] result.json 合法，含 case_id + 全部 output_contract 字段 + status/sources/uncertainty/change_log。
- [ ] 每个数值/引用/图表/关键论断在 evidence_map 有 locator + 逐字 quote，不新增源文外事实。
- [ ] 论断强度与源文匹配，相关未写成因果、初步未写成定论。
- [ ] 术语表完整，缩写首次展开、同义词收敛、跨文体映射齐全。
- [ ] change_log 覆盖每处删改，type 五类之一，理由可回答“删的是冗余还是证据”。
- [ ] 主产物可编辑且存在，Office/图表类附真实 PDF/PNG 预览。
- [ ] `python3 -B scripts/main.py --help` 退出码 0；`python3 -B scripts/selfcheck.py` 全部 PASS。

## 6. 降级与失败处理（不编造）

- 空输入目录：退出码 0，写合法 result（`insufficient_evidence`），不抛异常。
- 坏 JSON/坏编码/超大文件：记录到 `errors`/`degradations`，继续处理其余材料。
- 缺 `case_id`：status=`input_error`，uncertainty 写明原因，仍写合法 result。
- 缺软件（如渲染器/Office）：在 `limitations` 如实标注缺失项与影响，**不伪造**预览或软件状态；能产出的部分先产出。
- 材料冲突：两个来源都记录并标 `source_conflict`，进 `pending_human`，不擅自取舍。
- 重复运行：同一输入两次运行结果一致（确定性流程，脚本不引入随机）。
- 若运行中任何步骤失败，先 `main.py finalize` 兜底落盘 partial 结果再退出。

## 7. 最小调用示例（示意输出，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py init          # 落盘 result.json 骨架
python3 -B scripts/main.py dump          # 查看发现的材料与 case 元数据
# ……Agent 执行步骤 1-4，改写并回填 result.json……
python3 -B scripts/main.py finalize      # 校验并定稿
```

示意结果片段（标明为示例）：

```json
{"case_id": "example-001", "status": "partial",
 "artifacts": [{"path": "/app/output/draft.md", "format": "md", "description": "示例改写稿"}],
 "evidence_map": [{"item": "n=120", "locator": "paper.md#p3", "quote": "120 patients", "kind": "number", "strength": "fact"}],
 "change_log": [{"locator": "paper.md#p1", "before": "…", "after": "…", "reason": "删除背景铺陈", "type": "delete"}],
 "pending_human": [], "sources": [], "uncertainty": "示例"}
```
