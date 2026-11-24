---
name: sci-citation-management
description: 参考文献与引用管理的可复核 Skill：把真实稿件与真实文献库归一为可回溯的引用图中间表示，按“先校验、后变换”的顺序完成引用定位、去重归并、编号连锁更新、格式转换与一致性检查，交付可编辑稿件、同步文献表、逐条证据映射与差异记录。当需要整理、改写或校验学术稿件的引用与参考文献时使用。
version: 1.1.0
---

# 参考文献与引用管理 Skill

## 1. 角色与何时使用

本 Skill 面向“从真实稿件 + 真实文献库出发，完成引用管理并保留来源”的学术写作任务，覆盖题面四个核心问题：

- **挂得对**：识别需要来源支撑的论断句，给出引用位置与引用形式（顺序编码制 / 著者—出版年制 / 脚注），处理一句多引、多句一引。
- **编得顺**：插入、删除、调序、去重后重排编号，同步正文标记、文末列表序号与交叉引用，保持编号连续、样式统一。
- **收得净**：合并中英文题名、预印本与正式版、不同数据库导出的重复条目，给出可解释的合并判据。
- **对得上**：逐句判定正文论断与所引文献主题/结论是否匹配，检出未解决引用与未引用条目，标记证据不足。

不适用：一次性脚本；与引用无关的纯文本润色；没有材料却要求生成论文内容。

## 2. 输入契约（运行时）

- 原始输入固定为 `/input/case.json` 与 `/input/task.md`；材料可能在 `/input` 下任意子目录，**扫描发现，不假设文件名/键名**。
- 识别稿件 `.md/.txt/.tex/.docx/.pdf`；识别文献库 `.bib/.ris/.enw/.json/.csv/.tsv/.xlsx`。PDF 文本用运行时 `pdf-text-tool` 提取，本包脚本不内置 PDF 解析。
- 单文件 2 MB 上限，超限跳过并记录；文本按 `utf-8-sig` 容错读取，坏编码/坏 JSON 降级不崩溃。
- `case.json` 的 `output_contract` 是输出字段的**唯一权威**；工作目录 `/app/work`，最终产物全部落到 `/app/output/`。

## 3. 输出契约（必须产出）

`/app/output/result.json` 必须含：

1. `case_id`（取自 case.json）。
2. `output_contract` 的**全部字段原样回填**：能确定的填实际产物路径，不能确定的填 `null`/`[]`；不改名、不删未知键。
3. 附加字段：`status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`artifacts`、`runtime`。
4. 主产物（可编辑、可复核）：
   - `manuscript.md`：带重排后引用标记的可编辑稿件
   - `references.md`：与正文编号同步的文献表
   - `evidence_map.json`：每条引用 → `locator + 逐字 quote` + 匹配依据
   - `consistency_report.json`：未解决引用、未引用条目、编号问题
   - `citation_graph.json`：全部材料归一后的中间表示（CIR），供逐节点回溯
5. 每个关键结论/数值/引用必须有来源定位；冲突不隐藏（记 `source_conflict`）；缺失写 `not_reported`/`pending_human`，不编造。

## 4. 执行流程（900 秒 / 64 次模型请求 / 每次 ≤16384 tokens）

| 阶段 | 时间 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| 0 发现 | 0–60s | ≤4 | 扫 `/input`，读 case.json/task.md，列材料清单 |
| 1 落骨架 | 60–180s | ≤4 | **必须先跑** `python3 -B scripts/main.py`，落盘合法 `result.json` 骨架 + 空产物；之后只做增量改进 |
| 2 语义 | 180–600s | ≤30 | 识别论断句、插入/定位引用、逐句 claim↔文献匹配、去重复核 |
| 3 核验 | 600–840s | ≤16 | 编号/交叉引用/格式复核、DOI/元数据核验、一致性检查 |
| 4 终稿 | 840–900s | ≤6 | 更新 result.json（status/sources/uncertainty/change_log），复核产物 |

**前 3 分钟铁律**：立即运行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`，让 `result.json` 与产物先以合法骨架落盘；后续任何阶段中断都不会留下空目录或不完整交付。之后每次增量都把最新状态写回 `result.json`（幂等）。

脚本只做确定性部分（解析、去重、编号重排、格式转换、一致性检查）；语义判断（论断定位、claim↔文献匹配、来源核验）由 Agent 按 `references/citation_rules.md` 完成并回写 `evidence_map.json` 与 `change_log`，脚本不臆造这些结论。

## 5. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py                          # 缺省 /input -> /app/output
python3 -B scripts/main.py --style apa              # gbt7714 | apa
python3 -B scripts/main.py --self-test              # 内存自检，退出码 0
python3 -B scripts/selftest.py                      # 等价自检入口
```

缺参时 `--input/--output/--work` 分别指向 `/input`、`/app/output`、`/app/work`。脚本幂等：同一输入重复运行结果一致。

## 6. 自检清单（逐条对应评分维度）

- [ ] **实际效果·完成度**：交付可编辑稿件 + 同步文献表 + 证据映射 + CIR，覆盖任务要求的动作与全部引用点/条目。
- [ ] **实际效果·正确性**：每条引用可定位到文献库真实条目；元数据来自材料；论断与文献匹配；去重后编号仍指向幸存条目。
- [ ] **实际效果·完整性**：全部章节、引用点、条目都被处理，无漏项。
- [ ] **实际效果·可核验性**：evidence_map + change_log + citation_graph 能回溯原稿、文献库快照、匹配依据与每步改动。
- [ ] **使用增益**：编号/去重/格式由脚本自动完成，人工只做语义确认；同输入重复运行一致。
- [ ] **创新增益**：编号连锁更新跨去重边界、去重判据可解释、论断级匹配逐句记录、CIR 中间表示可逐节点回溯。
- [ ] **可靠体验**：`--self-test` 与 `--help` 退出码 0；空目录/坏 JSON/坏编码/缺字段不崩溃且如实记录。
- [ ] **生态贡献**：说明与提交一致，脚本/规则/清单可被他人独立复用。
- [ ] 详见 `references/checklist.md`，逐条勾选后才可交付。

## 7. 降级与诚实原则

- 缺文献库/缺稿件 → `status = insufficient_evidence`，`pending_human` 列出缺口，产物用占位符并标明“非真实结果”。
- 坏 JSON/坏编码/解析失败 → 记 `errors`；跳过超限/不支持文件 → 记 `degradations`，继续处理其余材料。
- DOI 解析失败、条目歧义、同名不同文献 → 不自动合并/不声称已核验，标 `not_verified`/`pending_human`/`source_conflict`。
- 缺 Office/PDF 渲染软件 → 交付 Markdown 可编辑版并标注 `limitations`，Office 渲染需运行时 `office-renderer`。
- **严禁编造**评测分数、DOI、元数据、检索结果或实验数据；`status` 如实，`ok` 只能在语义核验完成后设置。

## 8. 差异化设计（相对通用单脚本管线）

1. **中间表示（CIR）**：稿件与文献库先归一为 `citation_graph.json` 引用图，去重、重排、一致性全部在 CIR 节点上进行，每步可回溯。
2. **校验顺序前置**：preflight 在解析前校验编码/大小/字段；每个阶段先校验自身输出再交给下一阶段，坏输入只降级单个阶段。
3. **去重感知的编号重排**：去重返回“原索引 → 幸存索引”映射，稿件引用编号先经该映射再按首次出现顺序重排，避免去重后编号悬空。
4. **模块拆分**：`main.py`（编排）/ `core_impl.py`（纯引擎）/ `selftest.py`（内存自检）分离，均可独立复用。

## 9. 边界

一个 Skill 只解决“引用与参考文献整理”这一类任务；不代写不存在的实验/数据，不生成虚假参考文献，不做期刊排版模板适配。没有真实材料时输出“材料不足”，而不是示例填充。
