# 降级与不确定性规则（degradation & uncertainty）

本 Skill 的变体侧重是：**limitations / uncertainty 显式化，缺数据不编造**。
把「不知道」写成可复核的结构化字段，而不是用示例填充或静默跳过。

## 1. 两条铁律

1. **不编造**：没有真实材料、真实数值、真实引用、真实 DOI 时，写
   `MATERIAL_GAP` / `pending_human` / `not_reported`，绝不生成示例数据冒充正式结果。
2. **不确定就说**：每一处修改带 `confidence`，每条事实带 `certainty`，
   每个缺口进 `gaps[]`，最终汇总进 `result.json.uncertainty` 与
   `result.json.limitations`。

## 2. 三级置信（逐句编辑）

| confidence | 含义 | 处理 |
|---|---|---|
| `high` | 纯语法/格式修正，语义与事实可确定 | 直接采用 |
| `medium` | 涉及语域/术语/语序，需谨慎 | 写 `uncertainty_reason` |
| `low` | 语义或术语把握不足、材料含糊 | 保留原文或最小编辑，进 `pending_human` |

- `medium`/`low` 的 unit 自动进入 `uncertainty.items[]`；`low` 额外进入
  `pending_human[]`。
- 无法判断「该不该改」时，**不改**并注明，比改错更可取（最小编辑原则）。

## 3. 四级事实确定性（证据溯源）

| certainty | 含义 | 是否进 uncertainty |
|---|---|---|
| `verified` | 材料逐字片段支撑，quote 完整 | 否 |
| `paraphrased` | 同义改述，语义保持但非逐字 | 否（建议保留 quote） |
| `inferred` | 由材料推断，非直接陈述 | 是 |
| `unverified` | 未核对，来源缺失或模糊 | 是，且进 `pending_human` |

## 4. 状态阶梯（result.json.status）

| status | 触发条件 | 含义 |
|---|---|---|
| `ok` | errors==0，无未申明的 hedge 漂移，无缺口 | 可交付 |
| `partial` | 有 error 或未申明的 warning 或缺口，但主产物已生成 | 可继续编辑，边界已标注 |
| `insufficient_evidence` | 有任务说明但无底稿/材料 | 列缺口，不写示例内容 |
| `input_error` | 输入目录缺失或不可读 | 记录错误，退出码 0 |

- 状态由 `finalize` 依据 `check_report.json` **确定性推导**，Agent 不手写状态。
- 任何中途失败都必须先有 `partial` 骨架（前 3 分钟铁律），不留下空产物。

## 5. 缺口与冲突的标注词汇

| 标记 | 使用场景 | 去向 |
|---|---|---|
| `MATERIAL_GAP` | 材料缺失某数字/引用/章节 | `gaps[]` → `limitations` + `pending_human` |
| `not_reported` | 材料未报告某项（如未给样本量） | `gaps[]`，不补值 |
| `source_conflict` | 两个来源数值/结论不一致 | `gaps[]` 记录双方，不选边 |
| `pending_human` | 需作者确认（重新编号、术语取舍、强度调整） | `gaps[]` → `pending_human` |
| `opaque` | 二进制材料（PDF/DOCX）无法文本解析 | `sources[].type` + `limitations` |

## 6. 降级矩阵（缺数据 / 缺软件）

| 场景 | 处置 | 禁止 |
|---|---|---|
| 空输入目录 | `status=input_error`，退出码 0，列缺口 | 抛异常、留空 result.json |
| 无底稿 | `status=insufficient_evidence`，列缺口 | 写示例内容 |
| 坏 JSON / 坏编码 | 记入 `errors`/`degradations`，继续处理其余材料 | 崩溃 |
| PDF/DOCX 二进制 | `sources` 标 `opaque`；无解析工具标 `pending_human` | 按文件名猜内容 |
| 缺渲染软件 | 交付可编辑 `.md`/`.json` 主产物，`degradations` 注明「未生成 PDF/PNG 预览」 | 伪造 PDF |
| 材料冲突 | 双方记录 + `source_conflict` | 静默选边 |
| 文体/受众不明 | 最小编辑（只改硬语法错误），`uncertainty` 标注 | 擅自整段重写 |
| 超长输入 | 分章节逐句处理；单文件 >2MB 跳过并记录 | 截断后假装完整 |

## 7. result.json 的不确定性/限制字段

```json
{
  "uncertainty": {"summary": "...", "items": [
    {"locator": "draft.md#L3", "level": "low", "reason": "..."}
  ]},
  "limitations": ["binary source not text-parsed: materials/x.pdf"],
  "pending_human": [{"locator": "...", "reason": "MATERIAL_GAP"}]
}
```

- `uncertainty` 汇总脚本检测到的低置信、未核实事实与未申明 warning。
- `limitations` 汇总二进制源、缺口与未解决 warning，是**交付时的显式边界**。
- `pending_human` 是需要作者回填/确认的清单，不混入已确认内容。

## 8. 与参考实现的设计差异（本变体的取舍）

1. **不确定性是一等公民**：每个 unit 强制带 `confidence`，每条 fact 强制带
   `certainty`，脚本自动汇总，而非仅在 prose 里提一句。
2. **limitations 独立成字段**：与 `uncertainty` 分离——uncertainty 描述「本结果的可信度」，
   limitations 描述「本交付的已知边界」。
3. **缺口词汇可机检**：`MATERIAL_GAP`/`source_conflict`/`pending_human`/`not_reported`
   是受控词表，便于第三方复核与自动统计，避免自由文本掩盖缺口。
4. **状态由脚本推导**：`ok/partial/...` 不靠 Agent 自评，而是 `finalize` 根据
   `check_report` 确定性计算，防止「自报成功」。
5. **宁可不改**：`low` 置信时优先保留原文并标记，而不是给出一个看似流畅但可能改错的版本。

## 9. 一次完整走查（walkthrough，示意）

假设底稿有一句：`治疗后 12 h 血糖降至 6.2 ± 0.4 mmol/L [8]。` 但材料里没有给出
第 8 号引用的全文，且 `6.2 ± 0.4` 在材料与底稿间存在 0.1 的差异。

Agent 的处理与字段落点：

1. 该句的 unit：
   ```json
   {"id":"u07","locator":"draft.md#L4","original":"治疗后 12 h 血糖降至 6.2 ± 0.4 mmol/L [8]。",
    "revised":"治疗后 12 h 血糖降至 6.2 ± 0.4 mmol/L [8]。","edit_type":"preservation",
    "reason":"数值与引用存在材料冲突，暂不改动","confidence":"low",
    "uncertainty_reason":"底稿 6.2 ± 0.4 与材料 6.1 ± 0.4 冲突，需作者确认","hedge_justified":false}
   ```
2. fact 记录数字，`certainty="unverified"`，`quote` 用底稿逐字片段；冲突单独立 gap。
3. gap 记录：
   ```json
   {"locator":"materials/data.md#T2","type":"source_conflict",
    "detail":"血糖 6.2±0.4（底稿）vs 6.1±0.4（材料表2）"}
   ```
4. `pending_human` 条目：`{"locator":"draft.md#L4","reason":"source_conflict，需作者确认数值"}`。
5. 最终 `uncertainty.summary` 含「1 个低置信、1 条未核实事实、1 个缺口」；
   `limitations` 含「材料表2 与底稿数值冲突，未选边」；`status` 由 finalize 推导为
   `partial`（存在未解决 warning/gap）。

对比：若不经本流程，基础 Agent 常会静默把两个数值「统一」成其中一个，或在没有引用全文时
补一条不存在的信息。本 Skill 的做法是**留下冲突与缺口，交给作者**，而不是替作者决定。

