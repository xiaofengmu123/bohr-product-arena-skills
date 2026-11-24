# 写作与证据规则（正文起草用）

本文件约束 Agent 从材料到成稿的每一步，目标是把四段引言写"立得住、
分得清、说得实、对得上"。事实、数字与引用的纪律优先于文采。

## 1. 四段结构与承接

按固定顺序组织 manuscript.md，每段有明确可核对的功能：

1. **研究背景**：交代领域脉络、具体问题与技术约束，说明"为什么现在值得
   做"，落到一个真实的未解决问题。禁止"近年来受到广泛关注""具有重要意义"
   等空转铺垫。
2. **相关工作**：按问题设定 / 方法路线 / 数据与评价方式分组，组内按同一
   维度比较取舍，不按时间罗列。每组给出：问题设定、方法、数据与评价维度、
   结论与局限。
3. **研究空白**：从已引工作的共同限制、未覆盖场景或相互冲突的结论推出；
   写明本文工作如何填补，无法由文献推出的判断标"作者判断"。
4. **贡献**：把方法、实验、结论压缩为可核对的条目，每条都能映射到正文的
   方法 / 图表 / 结果，不夸大适用范围。

四段之间的因果要显式：背景的未解决问题 → 相关工作为何没解决 → 空白
是什么 → 本文贡献如何对应填补。

## 2. 事实与证据纪律

- 正文每个事实句挂 `[S#]`，S# 单调递增，与 evidence_map.json 一一对应。
- 数字、单位、统计量、限定词逐字复用材料；材料没有的不补，写
  `MATERIAL GAP` 或 `not_reported`。
- evidence_map 条目形如 `{"locator": "materials/paper.md#p3", "quote": "…逐字…",
  "kind": "fact|claim|quote|method|result"}`；locator 用 `路径#段落/页码`。
- 冲突信息不隐藏：同一指标两来源不一致时，两条都记录并标 `source_conflict`，
  正文选择时说明理由。

## 3. 引用纪律

- 引用只用材料中真实出现的来源；元数据（作者 / 年份 / DOI / 卷期页）缺失
  时写 `pending_human`，绝不臆造。
- 每条引用落在具体论断上，不在段落末尾堆引用。
- 引用格式：案例指定格式优先；缺省作者—年份；切换格式记 change_log。
- 转述保留原文结论、适用条件与局限，不把"在 X 条件下"写成"普遍"。

## 4. change_log 纪律（本包重点）

- result.json 的 `change_log` 为权威版本记录，同时镜像到 change_log.json。
- 每条必含 `{at, target, before, after, reason}`；`at` 用阶段名（如
  `skeleton` / `step4_draft` / `step5_verify`），同一 target 多次修改按序追加。
- 数值与单位保全清单可并入 `preserved_facts.json`：记录原文值 → 成稿值 →
  是否一致，便于回溯。
- 回填 result.json 时先读 `work/case_normalized.json` 核实合同字段；脚本
  抽取的字段与 case.json 不一致时，以 case.json 为准手工补全。

## 5. 空白与贡献的推导

- 空白必须由"已引文献的共同限制 / 未覆盖场景 / 冲突结论"推出，写清推导
  链条；不能由文献推出的判断标注"作者判断"。
- 贡献条目采用"能力 / 方法 → 对应证据"格式，例如"提出 X，对应正文第 Y 节
  方法、图 Z、表 W 结果"，避免笼统宣称。

## 6. 增量写作节奏（900 秒 / 64 次请求）

- 前 3 分钟只做发现 + 骨架落盘，此后任何中断都保留合法 partial 结果。
- 先建事实池 `work/facts.md`（每条带 locator + quote），再分组，再起草，
  最后逐条核对回填，不边读边写。
- 重复读取用 `work/` 缓存，不逐回合重读大 PDF；单文件 >2MB 已跳过并记录。

## 7. 结构模板（可复用骨架，不含任何材料内容）

以下模板只规定结构与字段，正文一律由材料推导，禁止照搬示例性文字。

**manuscript.md 四段骨架**

```markdown
# 引言与相关工作

## 1 研究背景
（领域脉络 → 具体问题 → 技术约束 → 未解决问题；事实句挂 [S#]）

## 2 相关工作
### 2.1 按问题设定分组
（组内比较：问题设定 / 方法 / 数据与评价方式 / 结论与局限）

### 2.2 按方法路线分组
（……）

## 3 研究空白
（由已引工作共同限制 / 未覆盖场景 / 冲突结论推出；写明本文填补方式；
 非文献推导的判断标注"作者判断"）

## 4 贡献
1. （贡献条目 1 —— 对应正文方法 / 图表 / 结果）
2. （贡献条目 2 —— 对应……）
```

**evidence_map.json 条目模板**

```json
{
  "S1": {
    "locator": "materials/paper.md#p3",
    "quote": "材料中的逐字片段",
    "kind": "fact | claim | method | result | quote"
  }
}
```

**change_log.json 条目模板（与 result.json.change_log 同步）**

```json
{
  "at": "step4_draft",
  "target": "manuscript.md",
  "before": "",
  "after": "新增研究背景段",
  "reason": "由材料背景段归纳，未新增事实"
}
```

**preserved_facts.json 条目模板（数值/单位保全）**

```json
{
  "item": "关键数值/单位",
  "source": "materials/paper.md#p5",
  "original": "材料原值",
  "in_manuscript": "成稿中的值",
  "preserved": true,
  "note": "是否一致及原因"
}
```

**result.json 最小合法骨架（脚本自动产出）**

```json
{
  "case_id": "<case_id 或 null>",
  "<contract_field>": null,
  "status": "partial",
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [],
  "artifacts": [],
  "errors": [],
  "degradations": [],
  "runtime": 0.0
}
```

以上模板为可复用资源，任何实际案例都必须替换为真实材料推导出的内容，
不得保留占位文字。
