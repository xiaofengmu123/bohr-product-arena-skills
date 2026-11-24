# 引言与相关工作：证据、写作与降级细则

本文是 `SKILL.md` 第 5、6 节的展开版，供 Agent 在执行时逐条对照；以 SKILL.md 为准，冲突处按 SKILL.md 处理。

## 1. 四段结构与四条硬标准

| 段落 | 硬标准 | 判定依据 |
|---|---|---|
| 研究背景 | 立得住 | 指向真实研究脉络与具体未解决问题，交代“为什么现在值得做”；禁止“近年来受到广泛关注”“具有重要意义”式空转 |
| 相关工作 | 分得清 | 按问题设定/方法路线/数据与评价方式分组，同组内同一维度比较取舍；禁止逐篇摘要拼接、按时间罗列 |
| 研究空白 | 说得实 | 由已引工作的共同限制、未覆盖场景或相互冲突结论推出，并写明本文如何填补；无法由文献推出处标“作者判断” |
| 贡献 | 对得上 | 每条贡献压缩为可核对条目，能映射到正文方法/图表/结果；不夸大适用范围 |

## 2. 证据与溯源规则

- 每个关键结论、数值、图元、引用都挂 `[S#]`（如 `[S1]`），`evidence_map.json` 中 `S#` → `{locator, quote, kind}`。
- `locator` 形如 `materials/paper.md#p3`（文件名 + 段落号）；`quote` 必须是材料**逐字**片段，不允许复述代替。
- `kind` 取值建议：`fact`（事实）、`number`（数值）、`claim`（论断）、`citation`（引用）、`figure`（图表）、`method`（方法）。
- 数字、单位、统计量、限定词逐字复用；材料没有的不补，写 `MATERIAL GAP` 或 `not_reported`。
- 冲突信息不隐藏：同一指标两个来源不一致时，两条都记录并标 `source_conflict`，正文不擅自取其一。
- 引用只用材料中真实出现的来源；元数据缺失写 `pending_human`，不造作者/年份/DOI。

## 3. result.json 合同回填细则

- 合同字段以 `case.json` 为准，按优先级发现：`output_contract` → `output` → `expected_output` → `fields` → `required_fields`（`bootstrap.py` 已做，结果见 `work/case_normalized.json`）。
- 字段名为字符串 → result.json 顶层键，值缺省 `null`；为对象 → 按子键结构递归回填；为列表 → 回填 `[]`。
- 合同字段名与标准字段名冲突时，**合同字段优先**：标准字段不覆盖已存在的同名键。
- `bootstrap.py` 的 `contract_fields` 为最佳努力抽取；Agent 必须读 `work/case_normalized.json` 核实，缺失字段手工补进 result.json。
- `status` 判定：四段齐全 + 证据齐全 → `ok`；有可用稿但存缺口 → `partial`；材料不足以成稿 → `insufficient_evidence`；输入无法解析 → `input_error`。

## 4. 标准字段契约

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string/null | 来自 case.json，缺失为 `null` 并记 `input_error` |
| `status` | string | `ok / partial / insufficient_evidence / input_error` |
| `sources` | list | `{key, locator, quote, kind}`，逐条可回溯 |
| `uncertainty` | list | `{item, reason}`，材料不足/冲突/未证实处 |
| `change_log` | list | `{at, target, before, after, reason}`，可回退 |
| `artifacts` | list | 产物文件路径（manuscript.md、evidence_map.json、change_log.json 等） |
| `pending_human` | list | 需人工确认项（引用元数据、歧义表述等） |
| `errors` | list | 输入/运行错误 |
| `degradations` | list | 降级处置记录（超限跳过、坏编码、PDF 不可用等） |
| `runtime` | object | 实际耗时等运行信息（由 Agent 回填，脚本不写时间戳以保证确定性） |

## 5. 降级处置表

| 场景 | 处置 |
|---|---|
| 材料不足/文献库为空 | `insufficient_evidence`；只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充 |
| 引用查不到原文 | `pending_human` + `uncertainty`，不臆造元数据 |
| 目标期刊格式未知 | 作者—年份 + `uncertainty` 声明假设，不冒充特定格式 |
| 语言切换 | 术语/数字/引用原位保留，仅换语，限定强度不变 |
| PDF/软件不可用 | `degradations` 记录，用现有文本继续，不伪造缺失状态 |
| 单文件 >2MB | 跳过并记 `degradations` |
| 坏 JSON/坏编码 | 降级读取并记 `errors`/`degradations`，其余材料继续处理 |
| 写盘失败 | 最小合法 result.json（`input_error`+`errors`）收尾 |

## 6. 确定性与请求预算

- 三个脚本均为纯标准库、无随机成分；bootstrap 不向 result.json 写时间戳，同一输入重复运行字节级一致。
- 总预算 900 秒 / 64 次模型请求；前 3 分钟先跑 `bootstrap.py` 落盘骨架，之后只做增量改进。
- 每个模型回合前先读已写文件（`work/facts.md`、`manuscript.md`、`result.json`），不逐回合重读大 PDF；大材料只读一次并缓存到 `work/`。

## 7. 维护信息

- 适用：中英文科研论文引言与相关工作的起草、改写、补引用、核对、分组比较。
- 支持引用格式：案例指定格式优先；缺省作者—年份，可改数字式（改动记 `change_log`）。
- 限制：不做期刊排版模板适配；不生成材料外事实；不自动下载或联网补参考文献。
- 依赖：仅 Python 3.9+ 标准库，无第三方包。
