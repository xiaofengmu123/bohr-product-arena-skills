---
name: sci-intro-writing-lean
description: 依据科研材料撰写、改写、补引用或核对论文“引言与相关工作”，按“研究背景—相关工作—研究空白—贡献”四段组织，把每条论断、数字与引用绑定到来源证据，交付可编辑稿件与可复核 result.json。当需要写引言、整理相关工作、补引用、核对贡献，或把逐篇摘要改成按问题/方法分组比较时使用。
version: 1.1.0
---

# 引言与相关工作写作 Skill（精简可读版）

## 1. 角色与何时使用

本 Skill 把“引言与相关工作”写作沉淀为可复核流程，四条硬标准：**立得住**（背景指向具体未解决问题）、**分得清**（相关工作按问题/方法/技术路线分组比较）、**说得实**（空白由已引文献缺口推出）、**对得上**（贡献对应正文方法/图表/结果）。

适用指令：写引言、整理相关工作、补引用、核对贡献、把逐篇摘要改成按路线分组比较、双语改写。
不适用：期刊排版模板（LaTeX/Word 版式）、没有真实材料却要求生成完整综述、纯闲聊问答。

## 2. 输入契约

运行时固定目录：输入 `/input`、工作区 `/app/work`、产物 `/app/output`。主动扫描，不假设文件名：

- `/input/case.json`：案例任务、受众/文体/格式约束、`output_contract`（可能位于顶层或 `output_contract`/`output`/`expected_output`/`fields`/`required_fields` 之下）。
- `/input/task.md`：题目要求与评分说明。
- 其余材料（`materials/`、`notes.md`、`paper.md`、PDF、`.bib` 等）按实际文件内容读取，不依据文件名标签假设；PDF 用平台提供的解析工具，不臆测提取层内容。

约束：单文件 >2MB 跳过并记入 `degradations`；坏 JSON/坏编码降级读取并记录；材料冲突不隐藏，两条都记录并标 `source_conflict`。

## 3. 输出契约

最终必须写 `/app/output/result.json`（合法 JSON）：

- `case_id`：来自 case.json（缺失则 `null` 并记 `input_error`）。
- 回填 `output_contract` 的**全部字段**：字段名原样复制为 result.json 顶层键，不改名、不删键；值为对象则按其子键结构回填，值为列表则回填 `[]`，未产出/未知值填 `null`。
- 标准字段（必须有）：`status`、`sources`、`uncertainty`、`change_log`、`artifacts`、`pending_human`、`errors`、`degradations`、`runtime`。
  - `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实取值。
  - `sources[]`：`{key, locator, quote, kind}`；`quote` 为材料逐字片段，`locator` 形如 `materials/paper.md#p3`。
  - `uncertainty[]`：`{item, reason}`；`change_log[]`：`{at, target, before, after, reason}`。

同目录交付可编辑主产物与佐证（文件名按案例要求，缺省如下）：

- `manuscript.md`：可直接进入论文的成稿，四段齐全且承接。
- `evidence_map.json`：`[S#]` → `{locator, quote, kind}`，正文每个事实句挂 `[S#]`。
- `change_log.json`：组装/改写记录（数值与单位保全清单并入）。

## 4. 执行流程（900 秒 / 64 次模型请求）

总预算 900 秒、64 次模型请求。**前 3 分钟内必须先落盘一版合法 partial 结果骨架，之后只做增量改进**，任何中途异常都不留空产物。每个模型回合前先读已写文件，避免重复劳动。

1. **引导（0–120 秒，≤1 次请求）**：`python3 -B scripts/bootstrap.py`。扫描输入、写 `work/input_manifest.json` 与 `work/case_normalized.json`，并落盘 `result.json` 骨架（`status=partial`，合同字段填 `null`/`[]`）；空输入自动落 `insufficient_evidence`。
2. **建事实池与分组（120–300 秒，6–12 次请求）**：逐份读材料，摘录事实/数字/引用到 `work/facts.md`，每条带 locator+逐字 quote；相关工作按问题设定/方法路线/数据与评价方式分组。
3. **起草四段（240–480 秒，12–24 次请求）**：写 `manuscript.md`（研究背景、相关工作、研究空白、贡献）；事实句挂 `[S#]`，引用落在具体论断上；同步写 `evidence_map.json` 与 `change_log.json`。
4. **核对回填（60–180 秒，6–12 次请求）**：逐条核对数字/单位/限定词/引用元数据；空白须由文献缺口推出、作者判断须标注；把结果写入 result.json 合同字段，`status` 取 `ok`/`partial`/`insufficient_evidence`。
5. **校验封存（≤60 秒，1–2 次请求）**：`python3 -B scripts/validate.py` 自愈缺失字段并出报告；按报告修正后重写 result.json。

请求预算只用于必要的读取与写作；重复读取用 `work/` 缓存，不逐回合重读大 PDF。

## 5. 自检清单（交付前逐条勾选）

- [ ] 四段齐全（研究背景/相关工作/研究空白/贡献）且逻辑承接，可直接进入论文。
- [ ] 背景指向具体未解决问题，无“近年来受到广泛关注”式空转。
- [ ] 相关工作按问题/方法/技术路线分组，含问题设定、方法、数据与评价维度；转述与原文一致。
- [ ] 每条引用落在具体论断上，元数据（作者/年份/来源）真实可核对。
- [ ] 空白由已引文献共同限制/未覆盖场景/冲突结论推出，并写明本文填补方式；作者判断已标注。
- [ ] 每条贡献可映射到正文方法/图表/结果，不夸大适用范围。
- [ ] 每句事实有 `[S#]`，evidence_map 的 locator+quote 逐条可回溯。
- [ ] result.json 含 case_id + 合同全部字段 + status/sources/uncertainty/change_log。
- [ ] 无编造数据、引用、DOI 或评测分数。
- [ ] 重复运行结果一致（确定性；脚本不写时间戳进 result.json）。

## 6. 降级策略（如实标注，不编造）

- 材料不足/文献库为空 → `status=insufficient_evidence`，只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充。
- 引用查不到原文 → `pending_human` + `uncertainty`，不臆造作者/年份/DOI。
- 目标期刊格式未知 → 采用作者—年份引用，`uncertainty` 声明假设，不冒充特定格式。
- 语言切换 → 术语/数字/引用原位保留，仅换语，限定强度不变。
- PDF/软件不可用 → 记 `degradations`，用现有文本继续，不伪造缺失状态。
- 写盘失败 → 最小合法 result.json（`status=input_error`+`errors`）收尾，不让进程崩溃。

## 7. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/bootstrap.py      # 缺省 --input /input --output /app/output --work /app/work
python3 -B scripts/validate.py       # 校验并自愈缺失字段
python3 -B scripts/selftest.py       # 自检：空输入/坏 JSON/合同回显/自愈/坏编码
```

脚本仅做发现/骨架/校验/自检，正文写作由 Agent 完成。详细证据规则与降级细则见 `references/rules.md`。
