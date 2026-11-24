# 自检清单、降级处置与合同回填细则

## 1. 交付前自检（对应评分维度，逐条勾选）

**实际效果（40 分）**
- [ ] `manuscript.md` 四段齐全且承接，可直接进入论文。
- [ ] 他人工作转述与原文一致，引用元数据真实可核对。
- [ ] 空白由已引文献缺口推出，贡献与实际方法/图表/结果相符。
- [ ] 背景、相关工作、空白、贡献、引用覆盖五要素齐全；主要方法路线、关键工作、必要字段（作者/年份/来源）覆盖。
- [ ] 每条引用可回到来源；分组依据、空白推导、贡献对照可独立复核。

**使用增益与创新（40 分）**
- [ ] 引用与论断绑定（`[S#]` → evidence_map）而非文末堆引用。
- [ ] 相关工作按路线分组比较，而非逐篇摘要拼接。
- [ ] 空白推导、贡献对照写为可复核的中间产物（`work/facts.md`、`evidence_map.json`）。
- [ ] 核对与写作分离：先事实池后成稿，改写轮次可追溯（`change_log.json`）。

**可靠体验（10 分）**
- [ ] 空输入目录：脚本退出码 0，写出 `insufficient_evidence`。
- [ ] 坏 JSON/坏编码：记录到 `errors`/`degradations`，其余材料继续处理。
- [ ] 缺参数：脚本缺省指向 `/input`、`/app/output`、`/app/work`。
- [ ] 重复运行：两次结果一致（无随机或已固定种子）。

**产物格式（10 分，生态）**
- [ ] `result.json` 可被 `json.load` 解析。
- [ ] `manuscript.md` 非空且无占位空话。
- [ ] `evidence_map.json` 每个 `[S#]` 有 locator + 逐字 quote。
- [ ] `limitations`/`uncertainty` 显式记录缺口，未用编造填充。
- [ ] 无编造数据、引用、DOI、评测分数。

## 2. result.json 合同回填细则

- 合同字段以 case.json 为准，按优先级发现：`output_contract` → `output` → `expected_output` → `fields` → `required_fields`。
- 字段名为字符串则作为 result.json 顶层键，值缺省 `null`；为对象则按对象子键结构回填；为列表则回填列表。
- 合同字段名与标准字段名冲突时，标准字段名让位：先保留合同原名字段，标准字段若重名则不覆盖。
- `main.py scan` 的 `contract_fields` 为最佳努力抽取；Agent 必须读 `work/case_normalized.json` 核实，缺失的合同字段手工补进 result.json。
- `status` 判定：
  - 四段齐全 + 证据齐全 + 无缺口 → `ok`
  - 有可用稿但存缺口/未确认 → `partial`
  - 材料不足以成稿 → `insufficient_evidence`
  - 输入无法解析 → `input_error`

## 3. 降级处置表

| 场景 | 处置 |
|---|---|
| 材料不足/文献库为空 | `insufficient_evidence`；正文只写可支撑内容，其余标 `MATERIAL GAP`，`limitations` 逐条说明 |
| 引用查不到原文 | `pending_human` + `uncertainty`，不臆造元数据 |
| 来源结论冲突 | 两条都记入 `sources` 标 `source_conflict`，正文标 `[存疑]` |
| 目标期刊格式未知 | 作者—年份 + `uncertainty` 声明假设 |
| 语言切换 | 术语/数字/引用原位，仅换语，限定强度不变 |
| PDF/软件不可用 | `degradations` 记录，用现有文本继续 |
| 单文件 >2MB | 跳过并记 `degradations` |
| 写盘失败 | 最小合法 result.json（`input_error`+`errors`） |

## 4. 最小调用示例（示例，非正式案例结果）

```bash
python3 -B scripts/main.py scan --input ./demo/input --work ./demo/work
python3 -B scripts/main.py emit --work ./demo/work --output ./demo/output
# Agent 在两步之间完成正文起草并回填 result.json
python3 -B scripts/main.py validate --work ./demo/work --output ./demo/output
python3 -B scripts/main.py selftest
```

示意输出（标明为示例，不冒充正式案例）：

```json
{"case_id": "demo-001", "status": "partial",
 "contract_fields": ["introduction", "related_work"],
 "sources": [{"key": "S1", "locator": "materials/paper.md#p2",
              "quote": "示例：材料逐字片段（此处为演示，非真实数据）", "kind": "quote"}],
 "limitations": [{"scope": "相关工作量", "missing": "示例缺口",
                  "impact": "示例影响", "disposition": "MATERIAL GAP"}]}
```

## 5. 维护信息

- 适用：中英文科研论文引言与相关工作的起草、改写、补引用、核对。
- 支持引用格式：案例指定优先；缺省作者—年份，可改数字式（改动记 `change_log`）。
- 限制：不做期刊排版模板适配；不生成材料外事实；不自动下载或联网补参考文献。
- 依赖：仅 Python 3.9+ 标准库，无第三方包。
