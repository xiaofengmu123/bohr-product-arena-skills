# 自检、降级与 output_contract 回填细则

本文件是 SKILL.md 的补充操作手册。Agent 在交付前必须逐条勾选自检；发生
降级时按表处置；回填 result.json 前先读 `work/case_normalized.json` 核实
脚本抽取的合同字段，而非盲目信任脚本。

## 1. 交付前自检（对应评分维度）

### 实际效果（40 分）
- [ ] manuscript.md 四段齐全（研究背景、相关工作、研究空白、贡献）且彼此承接，可直接进入论文。
- [ ] 他人工作转述与原文一致，结论、适用条件与局限都保留，未按时间顺序堆叠摘要。
- [ ] 研究空白由已引文献的共同限制 / 未覆盖场景 / 冲突结论推出，并写清本文如何填补。
- [ ] 每条贡献能映射到正文方法 / 图表 / 实验结果，不夸大适用范围。
- [ ] 背景指向具体未解决问题，无"近年来受到广泛关注"式空转。
- [ ] 背景、相关工作、研究空白、贡献、引用覆盖五要素齐全；主要方法路线、关键工作与必要字段（作者 / 年份 / 来源）已覆盖。

### 可靠体验（10 分）
- [ ] 空输入目录：`scripts/main.py` 退出码 0，写出 `insufficient_evidence`。
- [ ] 坏 JSON / 坏编码：记录到 `errors` / `degradations`，其余材料继续处理，不崩溃。
- [ ] 缺参数：脚本缺省指向 `/input`、`/app/output`、`/app/work`。
- [ ] 重复运行：两次结果结构一致（无随机；`runtime` / 时间戳为唯一变化项）。
- [ ] 契约字段与标准字段重名时，契约字段不被覆盖。

### 产物格式
- [ ] result.json 可被 `json.load` 解析，含 case_id + 合同全部字段 + status/sources/uncertainty/pending_human/change_log/artifacts/errors/degradations/runtime。
- [ ] manuscript.md 非空、无占位空话。
- [ ] evidence_map.json 每个 `[S#]` 有 locator + 逐字 quote。
- [ ] change_log 为数组，每条含 at/target/before/after/reason，可回退。
- [ ] 无编造数据、引用、DOI、评测分数；缺失处写 `MATERIAL GAP` / `not_reported` / `pending_human`。

## 2. output_contract 回填细则

- 合同字段按优先级发现：`output_contract` → `output` → `expected_output` → `fields` → `required_fields`，并递归搜索一层 wrapper。
- 回填规则：
  - 字段值为字符串 → 顶层键，缺省 `null`；
  - 值为对象 → 按子键结构递归回填（叶子 `null`、数组 `[]`、对象 `{}`）；
  - 值为列表 → 回填 `[]`；
  - 值为 `[{name/type}]` 规格列表 → 每个 name 成为顶层键，按 type 给 `null`/`[]`/`{}`。
- 脚本抽取为最佳努力；Agent 必须读 `work/case_normalized.json` 核实 `contract_key` / `contract_fields` / `contract_template`，发现偏差手工补进 result.json。
- `status` 判定：四段齐全 + 证据齐全 → `ok`；有可用稿但存缺口 → `partial`；材料不足以成稿 → `insufficient_evidence`；case.json 存在但无法解析 → `input_error`。

## 3. 降级处置表

| 场景 | 处置 |
|---|---|
| 材料不足 / 文献库为空 | `insufficient_evidence`；正文只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充 |
| 引用查不到原文 | `pending_human` + `uncertainty`，不臆造作者 / 年份 / DOI |
| 目标期刊格式未知 | 作者—年份 + `uncertainty` 声明假设，不冒充特定格式 |
| 语言切换 | 术语 / 数字 / 引用原位保留，仅换语，限定强度不变 |
| PDF / 软件不可用 | `degradations` 记录，用现有文本继续，不伪造缺失状态 |
| 单文件 >2MB | 跳过并在 `degradations` 记录，不静默丢弃 |
| 写盘失败 | 最小合法 result.json（`input_error` + `errors`）收尾，不让进程崩溃 |

## 4. 最小调用示例（示例，非正式案例）

```bash
python3 -B scripts/main.py scan --input /input --work /app/work
python3 -B scripts/main.py skeleton --work /app/work --output /app/output
# Agent 在两次脚本之间完成正文起草并回填 result.json
python3 -B scripts/main.py validate --work /app/work --output /app/output
python3 -B scripts/selftest.py     # 空目录 / 坏 JSON / 嵌套契约 / 确定性
```

示意输出（标明为示例，不冒充正式案例）：

```json
{"case_id": "demo-001", "status": "partial",
 "manuscript": null, "evidence_map": null,
 "sources": [{"key": "S1", "locator": "materials/paper.md#p2",
              "quote": "示例：材料逐字片段（此处为演示，非真实数据）", "kind": "fact"}],
 "change_log": [{"at": "skeleton", "target": "result.json",
                  "before": "", "after": "partial", "reason": "bootstrap"}]}
```

## 5. 维护信息

- 适用：中英文科研论文引言与相关工作的起草、改写、补引用、核对。
- 引用格式：案例指定格式优先；缺省作者—年份，可改数字式（改动记 change_log）。
- 限制：不做期刊排版模板适配；不生成材料外事实；不自动联网补参考文献。
- 依赖：仅 Python 3.9+ 标准库，无第三方包。
