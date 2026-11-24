# 自检清单、降级与合同回填细则

## 1. 交付前自检（对应评分维度）

**实际效果**
- [ ] manuscript.md 四段齐全且承接，可直接进入论文。
- [ ] 他人工作转述与原文一致，引用元数据真实。
- [ ] 空白由已引文献缺口推出，贡献与实际结果相符。
- [ ] 背景、相关工作、空白、贡献、引用覆盖五要素齐全；主要方法路线、关键工作、必要字段（作者/年份/来源）已覆盖。
- [ ] 每条引用可回到来源；分组依据、空白推导、贡献对照可独立复核。

**可靠体验**
- [ ] 空输入目录：脚本退出码 0，写出合法结果（`input_error`）。
- [ ] 有 case.json 但无材料：写出 `insufficient_evidence`。
- [ ] 坏 JSON/坏编码：记录到 `errors`/`degradations`，其余材料继续处理。
- [ ] 缺参数：脚本缺省指向 `/input`、`/app/output`、`/app/work`。
- [ ] 重复运行：两次结果结构一致（`runtime` 除外）。

**产物格式**
- [ ] result.json 可被 `json.load` 解析。
- [ ] manuscript.md 非空且无占位空话。
- [ ] evidence_map.json 的每个 `[S#]` 有 locator + 逐字 quote。
- [ ] 无编造数据、引用、DOI、评测分数。

## 2. result.json 合同回填细则

- 合同字段以 case.json 为准，按优先级发现：`output_contract` → `output` → `expected_output` → `fields` → `required_fields`，支持嵌套。
- 字段名为字符串则作为 result.json 顶层键，值缺省 `null`；为对象则按对象子键结构回填；为列表则回填列表。
- 合同字段名与标准字段名冲突时，标准字段让位：先保留合同原名字段，标准字段若重名则不覆盖；唯一例外是 `status` 始终写诚实值。
- `scan` 的 `contract_fields` 为最佳努力抽取；Agent 必须读 `work/case_normalized.json` 核实，缺失的合同字段手工补进 result.json。
- `status` 判定：四段齐全+证据齐全→`ok`；有可用稿但存缺口→`partial`；材料不足以成稿→`insufficient_evidence`；输入无法解析→`input_error`。

### 2.1 标准字段 schema

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `sources` | array | 每项 `{key, locator, quote, kind}`，`quote` 逐字 |
| `uncertainty` | array | 每项 `{item, reason}` |
| `change_log` | array | 每项 `{at, target, before, after, reason}`，`at` 用阶段标记 |
| `artifacts` | array | 交付产物路径列表 |
| `pending_human` | array | 需人工确认项 |
| `errors` | array | 运行错误记录 |
| `degradations` | array | 降级记录 |
| `runtime` | number/null | 运行期测量（唯一允许随运行变化的字段） |

### 2.2 回填示例（示意，非正式案例）

```json
{
  "case_id": "demo-001",
  "introduction": "……[S1]……",
  "related_work": "……[S3]……",
  "status": "ok",
  "sources": [{"key": "S1", "locator": "materials/paper.md#p2",
               "quote": "示例：材料逐字片段（此处为演示，非真实数据）", "kind": "fact"}],
  "uncertainty": [],
  "change_log": [],
  "artifacts": ["manuscript.md", "evidence_map.json", "change_log.json"],
  "pending_human": [],
  "errors": [],
  "degradations": [],
  "runtime": 42.1
}
```

## 3. 降级处置表

| 场景 | 处置 |
|---|---|
| 材料不足/文献库为空 | `insufficient_evidence`；正文只写可支撑内容，其余标 `MATERIAL GAP` |
| 引用查不到原文 | `pending_human` + `uncertainty`，不臆造元数据 |
| 目标期刊格式未知 | 作者—年份 + `uncertainty` 声明假设 |
| 语言切换 | 术语/数字/引用原位，仅换语，限定强度不变 |
| PDF/软件不可用 | `degradations` 记录，用现有文本继续 |
| 写盘失败 | 最小合法 result.json（`input_error`+`errors`） |
| 超时/超请求 | 停在最近检查点，未完成部分写 `limitations`/`pending_human` |

## 4. 阶段预算与 checkpoint 记账

| 阶段 | 时间 | 请求 | 检查点产物 |
|---|---|---|---|
| P0_scan | ≤90s | ≤3 | `work/input_manifest.json` + `work/case_normalized.json` |
| P1_skeleton | ≤90s（累计180s） | ≤2 | `output/result.json`(v0) + `work/budget_state.json` |
| P2_facts | ≤180s | ≤12 | `work/facts.md` |
| P3_draft | ≤240s | ≤20 | `output/manuscript.md` + `evidence_map.json` + `change_log.json` |
| P4_verify | ≤180s | ≤16 | `result.json` 合同字段回填 |
| P5_finalize | ≤120s | ≤4 | `work/validation_report.json` + 最终 `result.json` |

### 4.1 每阶段 do / don't

| 阶段 | do | don't |
|---|---|---|
| P0 | 只读、只记 locator 与合同字段 | 不写作、不臆测材料内容 |
| P1 | 先落盘合法 JSON 骨架 | 不在骨架前开始长写作 |
| P2 | 逐字摘录、标冲突 | 不改写数字、不补材料外事实 |
| P3 | 按分组矩阵起草、挂 `[S#]` | 不逐篇罗列、不夸大适用范围 |
| P4 | 逐条核对数字/单位/引用 | 不臆造 DOI/作者/年份 |
| P5 | 跑 validate、按报告修正 | 不跳过校验直接交付 |

## 5. 最小调用示例（示意，非正式案例）

```bash
python3 -B scripts/main.py scan --input ./demo/input --work ./demo/work
python3 -B scripts/main.py skeleton --work ./demo/work --output ./demo/output
python3 -B scripts/main.py checkpoint --work ./demo/work --phase P1_skeleton --requests 2
# Agent 在脚本之间完成正文起草并回填 result.json
python3 -B scripts/main.py validate --work ./demo/work --output ./demo/output
python3 -B scripts/main.py selftest
```

示意输出（标明为示例，不冒充正式案例）：

```json
{"case_id": "demo-001", "status": "partial",
 "contract_fields": ["introduction", "related_work"],
 "sources": [{"key": "S1", "locator": "materials/paper.md#p2",
              "quote": "示例：材料逐字片段（此处为演示，非真实数据）", "kind": "fact"}]}
```

## 6. 维护信息

- 适用：中英文科研论文引言与相关工作的起草、改写、补引用、核对。
- 引用格式：案例指定优先；缺省作者—年份，可改数字式（改动记 change_log）。
- 限制：不做期刊排版模板适配；不生成材料外事实；不自动下载或联网补参考文献。
- 依赖：仅 Python 3.9+ 标准库，无第三方包。
