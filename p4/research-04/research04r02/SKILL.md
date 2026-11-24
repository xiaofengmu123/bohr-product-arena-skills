---
name: research-frontier-trend-checkpoint
description: 面向“研究前沿与趋势分析”的可复核综述 Skill。把给定文献组织为时间趋势、主题演化、代表性工作与研究空白四类可检查产物，每条判断回链原文（locator + 逐字 quote），显式区分材料事实、合理推断与无法确定，并以“预算账本 + 原子写盘 + 前 3 分钟落骨架”保证 900 秒/64 请求内任何中断都留有合法 partial 结果。当任务要求做文献综述、趋势分析、主题演化追踪、代表性工作选取或研究空白识别时使用；不用于一次性问答、纯润色或单篇论文审稿。
version: 1.1.0
---

# 研究前沿与趋势分析 Skill（checkpoint 版）

## 1. 角色与何时使用

本 Skill 把“这个方向现在做到哪、往哪走、哪里还空着”变成**可复核的综述产物**，而不是一份“检索结果前沿清单”。触发条件：任务要求“文献综述 / 研究前沿 / 趋势分析 / 主题演化 / 选题评估 / 研究空白识别”，或需要交付能回到原文逐条核对的趋势结论。

四条硬约束（对应“实际效果”40 分）：
1. 趋势必须由**可核对的年度分布**支撑，禁止把关键词热度当趋势、把检索前 N 条当前沿。
2. 演化必须说明**何时、因何变化**（出现/合并/分裂/衰退、术语漂移、同名异义），禁止把“换了说法”当新兴主题。
3. 代表作必须**覆盖各阶段且口径可说明**，禁止只用总被引排序，须覆盖长尾方法首创工作。
4. 空白必须给出**排除证据**（检索范围 + 纳排规则 + 反例排查），禁止把“没检索到”当空白。

不适用：一次性脚本、单篇论文审稿、语言润色。严禁生成预置答案、伪造评测分数/白板或编造缺失数据；每个案例独立运行，不跨案例复用结论。

## 2. 输入契约（运行时）

- 原始输入在 `/input/case.json` 与 `/input/task.md`；科研材料（论文 PDF、元数据、正文、摘要、检索结果集）由平台放在 `/input/`（常见子目录 `materials/`）或由 `case.json` 指定路径。
- **先读实际文件内容**，不依据文件名/领域标签臆测；扫描 `/input/` 全目录发现文件，不假设固定文件名。PDF 由平台 `pdf-text-tool`（或本包 `pdftotext`）解析到 `/app/work/materials/`；解析失败按第 8 节降级，不伪造文本层。
- 文本统一容错读取（`utf-8-sig`→`utf-8`→`gb18030`→`latin-1`），坏 JSON/坏编码自动降级；单文件 >2MB 跳过并记录，>200 文件采样并记录 `degradations`。
- 工作目录 `/app/work`（中间产物）；最终产物写到 `/app/output/`，其中 `result.json` 必产。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON）。若 `/input/case.json` 含 `output_contract`，**原样回填其全部字段**（缺值给 `null`/`[]`，不改名、不删未知键），再合并下列固定字段；无 contract 时仅用固定字段。字段字典见 `references/contract.md`。

固定字段：`case_id`、`status`（`ok|partial|insufficient_evidence|input_error`）、`research_question`、`protocol`（纳排规则 + 年份口径）、`time_trend`、`topic_evolution`、`representative_works`、`research_gaps`、`evidence_map`（每条 `{id,claim,locator,quote,source_file,kind,confidence}`）、`conflicts`、`sources`、`uncertainty[]`、`pending_human[]`、`change_log[]`、`limitations[]`、`errors[]`、`degradations[]`、`runtime`。同时产出可编辑主产物 `report.md`、证据映射 `evidence_map.json` 与可复算表 `tables/documents.csv`、`tables/year_distribution.csv`。

## 4. 预算账本与早落盘（本版核心）

总预算 **900 秒 / 64 次模型请求 / 16384 tokens/响应**。阶段预算由 `scripts/main.py discover` 写入 `/app/work/budget.json`；**每步开始前先读该账本**决定下一步，不凭感觉硬跑。阶段顺序固定：

| 阶段 | 命令 | 时间 | 请求 | 产物 |
|-|-|-|-|-|
| 0 discover | `main.py discover` | 0–180s | ≤4 | result.json 骨架 + manifest + budget |
| 1 baseline | `main.py baseline` | 180–360s | ≤6 | `_baseline` 机械抽取 |
| 2 deep_analysis | `main.py commit --file work/patch.json` | 360–720s | ≤40 | 四类核验结论 |
| 3 selfcheck | `selftest.py` | 720–820s | ≤8 | 自检报告 |
| 4 finalize | `main.py finalize` | 820–900s | ≤6 | 定稿 |

硬规则：**前 3 分钟（阶段 0）必须先落盘一版 `result.json` 骨架**，此后只做增量改进；任何中断都不留空白产物。每次写盘都是原子操作（临时文件 + `os.replace`），并 `revision++`、追加 `change_log`。阶段完成用 `main.py checkpoint --phase … --status done --requests N` 记账。

## 5. 执行流程

1. **发现与落骨架（0–180s，≤4 次请求）**：`python3 scripts/main.py discover`，扫描 `/input/`、解析 case.json/task.md、回填 output_contract、归一文本材料到 `/app/work/materials/`、写 `/app/work/manifest.json` 与 `/app/work/budget.json`、落盘 `/app/output/result.json` 骨架。产出：可交付 partial 结果 + 材料清单。
2. **机械基线（180–360s，≤6 次请求）**：`python3 scripts/main.py baseline`，确定性抽取每篇文档的年份/标题/被引、年度分布、术语频率种子、空白候选句（future work/limitation/unknown）、冲突候选句（however/in contrast/versus）、代表作候选。全部写入 `result.json._baseline` 并标 `_meta.machine=true`。
3. **四类深度分析（360–720s，约 40 次请求）**：按 `time_trend → topic_evolution → representative_works → research_gaps` 顺序逐篇读材料相关段落，把 `_baseline` 候选核验为结论；每完成一类写一个 `work/patch.json`，用 `main.py commit` 合并，`support/status/confidence` 逐条更新，`_meta.machine=false`。空白项必须写 `exclusion_evidence`（检索式 + 排除条件），区分证据排除与检索遗漏。
4. **自检（720–820s，≤8 次请求）**：`python3 scripts/selftest.py`，校验契约字段、枚举、四类产物、quote 可定位、evidence_ids 回链、无伪造分数；修复结构错误，无法定位的 quote 改为精确引用或置 `kind=not_reported` 并写入 uncertainty。
5. **收尾（820–900s，≤6 次请求）**：`python3 scripts/main.py report` 渲染 report.md/CSV，`python3 scripts/main.py finalize` 定稿 status/uncertainty/pending_human/limitations/change_log，复跑 selftest 确认通过；`status=ok` 仅在四类产物齐全且关键 claim 均有证据回链时给出。

## 6. 证据回链与结论边界

- 每个趋势判断/演化结论/空白主张的 `claims[]` 条目以 `evidence_ids[]` 指向 `evidence_map`；每条证据含 locator（形如 `materials/mat_001.txt#L12-L15`）+ 逐字 quote。
- 三类显式区分：直接可引用的写 `kind=reported`；需归纳的写 `inferred` 并在 rationale/evidence 说明；查不到写 `not_reported`，不补写。
- 冲突证据两方都记录并标 `source_conflict`，在 `conflicts` 给出分辨率或“未决”。

## 7. 自检清单（逐条勾选，对应评分维度）

- [ ] result.json 合法且含 case_id、output_contract 全部字段与固定字段（任务完成度/完整性）。
- [ ] 四类产物均非空且可打开：time_trend.distribution 有年度计数、topic_evolution 有阶段与依据、representative_works 有各阶段作品与口径、research_gaps 有排除证据（实际效果/完整性）。
- [ ] 每条非空 quote 为原文逐字引用，selftest 无“未定位”硬错误（结果可核验性）。
- [ ] 年份口径写在 `protocol.date_definition_note` 与 `time_trend.unit_note`（在线发表/见刊/预印本）（结果正确性）。
- [ ] 代表作不只用被引排序，criteria 含方法首创/里程碑/覆盖阶段（结果正确性）。
- [ ] 空白项均写 exclusion_evidence，`coverage_absence` 与 `open_question` 区分清楚（结果正确性）。
- [ ] 冲突证据均记录于 conflicts，未静默择一（结果完整性）。
- [ ] 无编造 benchmark score、无预置答案；status 与四类产物一致（可靠体验）。
- [ ] 无法确认项标 `not_reported`/`inferred` 或入 `pending_human`，不凭空补结论（结果正确性）。
- [ ] limitations 如实列出缺失材料、未核验类别、解析失败；errors/degradations 有值即说明（异常处理）。
- [ ] change_log 记录骨架落盘与每次修改；重复运行分析内容一致（可核验性/稳定性）。

## 8. 降级与失败处理

- **检索为空/缺材料**：`status=insufficient_evidence`，`research_gaps` 写“材料不足，无法区分空白与遗漏”，不编造结论。
- **年份缺失**：`distribution` 置空、`unit_note` 写“无出版年份元数据，待人工”，`pending_human` 记录；不猜年份。
- **主题严重歧义/同名异义**：term_mapping 记录全部变体，`uncertainty` 说明归并口径，`confidence=low`。
- **文献量爆炸**：脚本按 2MB/200 文件上限采样并记录 `degradations`；报告写明“样本边界”，不宣称全领域覆盖。
- **证据冲突/权限不足/PDF 不可读**：如实标 `source_conflict`/`unchecked`/`degradations`，写入 limitations，不静默择一、不伪造文本层。
- **脚本失败**：selftest 报错时优先手工修复；无法修复则保留已落盘骨架并如实标 status/limitations。任何情况下不伪造软件状态、数据、文献或分数；partial 结果必须标明边界。

## 9. 脚本与资源

- `scripts/main.py`：`discover | baseline | commit | checkpoint | report | finalize | --version`，纯标准库。
- `scripts/selftest.py`：提交前自检，退出码 0/1，写 `/app/work/selftest.json`。
- `scripts/core_impl.py`：共享工具层（I/O、契约、机械抽取）。
- `references/contract.md`：字段字典与枚举；`references/checklist.md`：清单与评分映射。

执行约定：若当前目录不是本包根目录，先 `cd` 到 Skill 安装目录（平台默认 `/skills/weekly-skill`），再运行上述相对路径命令；脚本默认 `--input /input --output /app/output --work /app/work`，可用参数覆盖。
