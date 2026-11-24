---
name: paper-structure-optimizer
description: 论文结构与逻辑优化 Skill。把稿件解析为"章节—段落—句子"三层文档模型，按研究问题、章节结构、论证链、段落关系、重复内容、结论边界六个维度把结构缺陷定位到句子级 locator，输出可执行编辑动作（移动/合并/拆分/收窄/补过渡/删重复），逐条附原文逐字引用与论据链，最终写入 /app/output/result.json。适用于结构诊断、论证链核对、结论边界检查与改稿；不适用于需新增实验或补造数据的任务。
version: 1.1.0
---

# 论文结构与逻辑优化

## 1. 角色与何时使用

本 Skill 在以下场景启用：任务要求对一篇完整学术稿件做结构诊断或结构优化，输出"问题定位 + 可执行编辑动作"，而非语言润色或整篇另起改写。依据给定稿件材料（通常含 task.md、case.json、稿件正文、图、表、参考文献或原始 PDF），完成四件事——诊得准、改得动、链得通、说得清。

- 诊得准：把结构问题定位到具体章节、段落、句子，并说明属于哪一类结构问题。
- 改得动：输出可执行编辑动作（移动到哪、与哪段合并、从哪里拆分、哪句论断需收窄），而不是另写一稿。
- 链得通：核对研究问题—方法—结果—结论的闭环，逐条论断是否都有对应论据。
- 说得清：每条判断指回原文 locator 与逐字 quote，作者能据此采纳、部分采纳或驳回。

不适用：需要新增实验、补造数据或检索新文献来填补核心空白；此时如实报告材料缺口，不补造内容。

## 2. 输入契约

- 原始输入：`/input/case.json`（案例元数据、任务说明、可能的 output_contract）与 `/input/task.md`。
- 材料文件：`/input/` 下按清单提供的稿件正文、PDF、图片、表格、统计输出、参考文献等。PDF 可能无预解析层，必须实际读取，不能凭文件名或领域标签猜测。
- 工作目录：`/app/work`（中间产物）；最终产物：`/app/output/`。
- 脚本位于本 Skill 根目录 `scripts/`；`main.py` 用自身绝对路径定位同目录模块，不依赖启动时工作目录，从任意 cwd 调用 `python /skills/weekly-skill/scripts/main.py` 均可。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，合法 JSON），结构如下：

- `case_id`：取自 case.json 的 `case_id` 或 `id`，原样保留；读不到时写 `unknown` 并在 uncertainty 注明。
- 题目 output_contract 的全部字段：从 case.json 的 `output_contract` 读取键名并逐字段填写；若未显式给出，按本 Skill 默认契约输出 `diagnosis`（结构诊断报告，Markdown 文本）、`edit_plan`（可执行编辑动作数组）、`argument_map`（论点—论据映射数组）、`coverage`（六维覆盖对象）、`version_boundary`（修改差异与人工确认项）、`artifacts`（可编辑文件与预览路径）。
- `status`：`complete` / `partial` / `failed` 之一（写作中途可用 `in_progress`）。
- `sources`：数组，逐条 {claim_id, claim, source_ref, quote, confidence}；`source_ref` 用 locator（见第 8 节），`quote` 为原文逐字引用（≤240 字符）。
- `uncertainty`：数组，逐条记录材料不足、冲突、未确认项及原因。
- `pending_human`：数组，逐条记录需作者确认的判断与选项（采纳/部分采纳/驳回）。
- `change_log`：数组，按时间顺序 {time, action, status}，至少含骨架落盘与每次增量修改。

可编辑产物（Markdown/LaTeX/docx 等）写到 `/app/output/`；Office/图类产物另存 PDF/PNG 预览供审阅。`result.json` 允许多次增量覆盖，每次写入保持 JSON 合法、字段完整。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

硬性要求：开始后 3 分钟内必须先落盘一版 result.json 骨架（status=in_progress，各契约字段空值，change_log 记首条），之后只做增量改进；任何失败都不允许不落盘。

- 步骤 0（0–1 分钟）：读 `/input/case.json` 与 `/input/task.md`，确认 case_id、任务类型、受众、格式与 output_contract。运行 `python scripts/main.py --scaffold-only`（或直接 `python scripts/main.py`，其首步即落盘骨架）。输出：/app/output/result.json 骨架。
- 步骤 1（1–4 分钟）：盘点并解析材料。运行 `python scripts/main.py`（内部调用 `scripts/parse.py`）生成 `/app/work/document_model.json`，把稿件切为章节—段落—句子三层并分配稳定 locator，同时输出确定性候选信号（因果/相关/hedge/claim/贡献句、疑似重复段落对），标记无法直接读取的 PDF/图片。输入：/input 文件；输出：document_model.json 与材料清单。
- 步骤 2（4–8 分钟）：证据核对与结构诊断。以 document_model 的候选信号为锚，逐条回读原文核对（PDF 用渲染/OCR，图片用 read_image），按第 8 节六维分类把缺陷定位到句子级 locator，剔除误报，补记脚本漏掉的真实缺陷。增量写回 result.json 的 sources 与 coverage。
- 步骤 3（8–12 分钟）：写诊断与编辑动作。产出 diagnosis（按维度组织，每条含 locator+quote+判定依据）与 edit_plan（移动/合并/拆分/收窄/补过渡/删重复等动作，标注从哪到哪）。写 argument_map（claim→论据→verdict）。每个论断附来源定位。
- 步骤 4（12–14 分钟）：收束版本与边界。填 version_boundary（改动 diff、需人工确认项）、uncertainty、pending_human，把阴性发现与未覆盖维度如实记录。
- 步骤 5（14–15 分钟）：自检与定稿。运行 `python scripts/selftest.py --result /app/output/result.json` 校验契约、可回溯性与六维覆盖；修复 FAIL/WARN；status 置 complete/partial/failed，写 verify_report.json。

剩余时间增量改进，优先补 sources 定位与 pending_human，不推翻已落盘内容。某步缺材料立即降级（第 6 节），不空转。

## 5. 自检清单（逐条勾选，对应评分维度）

实际效果（40 分）：

- [ ] 已产出 diagnosis 与 edit_plan，为作者可直接执行的修改方案，而非整篇改写稿（任务完成度）。
- [ ] 每条缺陷定位到具体章节/段落/句子，且类别判定与原文相符，无虚构缺陷、无漏掉真实断裂、无把风格偏好当结构错误（正确性）。
- [ ] 六维（研究问题、章节结构、论证链、段落关系、重复内容、结论边界）均已核查，缺项如实说明是否为稿件主要问题（完整性）。
- [ ] 每条判断与每个论断都有 locator+逐字 quote，可独立复核（可核验性）。

使用增益（25 分）：

- [ ] 诊断基于脚本解析的 document_model 候选信号，而非凭印象（质量/效率）。
- [ ] 骨架先行、增量改进，change_log 记录每次落盘（增益可验证）。

创新增益（15 分）：

- [ ] 脚本提供候选锚点与信号，模型只核对、补漏、剔除，不凭空生成（新增能力）。
- [ ] 六维问题分类、编辑动作词表、论点—论据映射、结论边界判据遵循统一规则文件（方法改进）。

可靠体验（10 分）：

- [ ] 触发条件明确、脚本纯标准库、路径有效、可从任意 cwd 运行（易用/稳定）。
- [ ] 缺章节、无参考文献、图/公式缺失、稿件截断、单位/统计不明等情况已标注并降级，未编造（异常处理）。

生态贡献（10 分）：

- [ ] 说明、依赖、配置、范围、限制齐全（文档完整）。
- [ ] 六维分类、编辑动作词表、locator 规范与检查清单可被其他写作 Skill 复用（可复用性）。

## 6. 降级策略

- 缺文件/缺软件（无 OCR、无渲染器、无 LaTeX）：在 result.json 的 uncertainty 与 version_boundary.gaps 如实标注"未提供/不可用"，相应字段留空并说明，status 置 partial；绝不伪造读数、软件状态或科学数据。
- 稿件缺章节、无参考文献、图/公式缺失、稿件被截断：作为结构问题或材料缺口记录，不臆造缺失内容。
- 图表/统计输出读数不清、单位或显著性不明：不猜测，标 uncertainty，正文用"[待确认：xxx]"或稳健表述。
- 引用找不到或文献不全：只写已有文献支持的论断，缺失部分标注"无文献支持"。
- 运行失败或超时：保留已落盘的 partial result.json（含 status、sources、uncertainty、change_log），说明失败原因。

## 7. 脚本调用（仅 Python 标准库）

- `python scripts/main.py --case /input/case.json --task /input/task.md --input /input --work /app/work --out /app/output`：骨架落盘 + 文档模型 + 自检。可加 `--scaffold-only` 只落骨架。
- `python scripts/parse.py --input /input --work /app/work`：生成 /app/work/document_model.json。
- `python scripts/selftest.py --result /app/output/result.json --out /app/output`：契约与可回溯性校验 → /app/output/verify_report.json。

环境变量可覆盖默认路径：CASE_JSON、TASK_MD、INPUT_DIR、WORK_DIR、OUTPUT_DIR、RESULT_JSON。禁止伪造评测分数或运行结果。

## 8. 定位规范与六维分类（详见 references）

locator 格式：`doc://<相对文件名>#sec<章>.para<段>.sent<句>`，图表用 `fig<序号>` / `tab<序号>`。逐字 quote ≤240 字符；冲突信息不隐藏，标 `source_conflict`。

六维与编辑动作词表、论点—论据映射与结论边界判据见 `references/structure-taxonomy.md`；locator 与证据溯源、冲突与缺口处理见 `references/locator-and-evidence.md`。
