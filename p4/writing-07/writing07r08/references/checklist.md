# 自检、降级与合同回填细则

本文件是 SKILL.md 第 5、6 节的展开；与 `scoring_alignment.md` 的维度标签同源。交付前逐条勾选。

## 1. 交付前自检（对应评分维度）

**实际效果（E）**
- [ ] `[E1]` manuscript.md 四段齐全（研究背景/相关工作/研究空白/贡献）且彼此承接，可直接进入论文。
- [ ] `[E2]` 他人工作转述与原文一致，结论、适用条件、局限逐项保留，引用元数据真实。
- [ ] `[E3]` 研究空白由已引文献的共同限制/未覆盖场景/冲突结论推出，并写明本文填补方式。
- [ ] `[E4]` 背景/相关工作/空白/贡献/引用覆盖五要素齐全；方法路线、关键工作、作者/年份/来源字段已覆盖。
- [ ] `[E5]` 每条引用回到来源；`evidence_map.json` 每个 `[S#]` 有 locator + 逐字 quote。
- [ ] `[E6]` 分组依据、空白推导、贡献对照关系可独立复核（写入 `group_rationale`/`gap_derivation`/`contribution_map`）。

**使用增益（U）**
- [ ] `[U1]` 背景无“近年来受到广泛关注”式空转，指向具体未解决问题。
- [ ] `[U2]` 引用落在具体论断上，而非文末堆引用。
- [ ] `[U3]` 前 3 分钟已用 `skeleton` 落盘合法 partial result.json，之后只增量。
- [ ] `[U4]` 大材料（>2MB 或 PDF 原文）只读一次，事实池复用，不逐回合重读。
- [ ] `[U5]` `change_log` 完整（`{at,target,before,after,reason}`），数值/单位保全清单并入 `preserved_facts.json`。
- [ ] `[U6]` `runtime` 只记预算常量，无墙钟时间戳。

**创新增益（I）**
- [ ] `[I1]` 相关工作按问题/方法/技术路线分组，同一维度比较取舍，非时间罗列。
- [ ] `[I2]` 每条贡献可映射正文方法/图表/结果，适用范围不夸大。
- [ ] `[I3]` 五段流水线（事实池→骨架→起草→核对→finalize）可独立运行。
- [ ] `[I4]` `validate` 机械校验锚点覆盖与四段结构，不依赖人工抽查。
- [ ] `[I5]` `scripts/selftest.py` 全 PASS（退出码 0）。
- [ ] `[I6]` SKILL.md 第 1/9 节写明边界与相对通用方案的价值。

**可靠体验（R）**
- [ ] `[R1]` 触发词可命中真实指令（写引言/整理相关工作/补引用/核对贡献/改写综述）。
- [ ] `[R2]` 重复运行两次结果一致（确定性；随机成分已固定种子或不存在）。
- [ ] `[R3]` 六类异常均有 `degradations`/`pending_human`/`errors` 记录，不编造。

**生态贡献（M）**
- [ ] `[M1]` SKILL.md 六节齐全，frontmatter 含 name/description/version。
- [ ] `[M2]` 脚本/证据表模板/分组模板/核对清单可被他人复用。
- [ ] `[M3]` 评分维度对齐表与失败分析成文（见 `scoring_alignment.md`）。

## 2. result.json 合同回填细则

- 合同字段以 case.json 为准，发现优先级：`output_contract → outputContract → output → expected_output → fields → required_fields`。
- 字段名为字符串 → 作为 result.json 顶层键，缺省 `null`；为对象 → 按子键结构递归回填（无 type 的对象递归；带 `type:array/list` → `[]`，`type:object/dict/map` → `{}`）；为列表 → 回填 `[]`。
- 合同字段名与标准字段名冲突时，标准字段让位（合同名优先，标准字段不覆盖同名键）。
- `scan.py`（即 `main.py scan`）的 `contract_fields` 为最佳努力抽取；Agent 必须读 `work/case_normalized.json` 核实，缺失的合同字段手工补进 result.json。
- `status` 判定：四段齐全 + 证据齐全 → `ok`；有可用稿但存缺口 → `partial`；材料不足以成稿 → `insufficient_evidence`；输入无法解析 → `input_error`。

## 3. 证据溯源细则

- 每个关键结论/数值/图元指向材料的**逐字片段**：`{key, locator, quote, kind}`，locator 形如 `materials/paper.md#p3`。
- 数字/单位/统计量逐字复用，材料没有的不补，写 `MATERIAL GAP` 或 `not_reported`。
- 冲突信息不隐藏：同一指标两个来源不一致时，两条都记录并标 `source_conflict`。
- 引用只用材料中真实出现的来源；元数据缺失写 `pending_human`，不造 DOI/作者/年份。

## 4. 降级处置表

| 场景 | 处置 |
|---|---|
| 材料不足/文献库为空 | `status=insufficient_evidence`；正文只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充 |
| 引用查不到原文 | `pending_human` + `uncertainty`，不臆造元数据 |
| 目标期刊格式未知 | 作者—年份引用 + `uncertainty` 声明假设；不冒充特定格式 |
| 语言切换 | 术语/数字/引用原位，仅换语，限定强度不变 |
| PDF/软件不可用 | `degradations` 记录，用现有文本继续，不伪造缺失状态 |
| 单文件 >2MB | 跳过并记入 `degradations` |
| 坏 JSON/坏编码 | 捕获记录到 `errors`/`degradations`，其余材料继续处理 |
| 写盘失败 | 最小合法 result.json（`status=input_error` + `errors`）收尾，进程不崩溃 |
| 空白缺乏依据 | 明确标“作者判断”，不得冒充文献推出 |

## 5. 最小调用示例（示例，非正式案例结果）

```bash
# 本地演示（目录为示例，非正式案例）
python3 -B scripts/main.py scan --input ./demo/input --work ./demo/work
python3 -B scripts/main.py skeleton --work ./demo/work --output ./demo/output
# Agent 在两次脚本之间完成正文起草并回填 result.json
python3 -B scripts/main.py finalize --work ./demo/work --output ./demo/output
python3 -B scripts/selftest.py
```

示意输出（标明为示例，不冒充正式案例）：

```json
{"case_id": "demo-001", "status": "partial",
 "contract_fields": ["introduction", "related_work"],
 "sources": [{"key": "S1", "locator": "materials/paper.md#p2",
              "quote": "示例：材料逐字片段（此处为演示，非真实数据）", "kind": "fact"}]}
```

## 6. 可复用模板（evidence_map / 分组 / 空白 / 贡献）

`evidence_map.json` 缺省结构（字段按案例要求可裁剪，锚点必须带 `locator` + 逐字 `quote`）：

```json
{
  "anchors": {
    "S1": {"claim": "事实句转写", "claim_type": "fact|inference|author_judgment",
           "locator": "materials/paper.md#p3", "quote": "材料逐字片段",
           "numbers_preserved": true, "strength": "reported|inferred"}
  },
  "group_rationale": [
    {"group": "分组名", "dimension": "problem|method|data_eval",
     "members": ["ref_key_a", "ref_key_b"], "comparison": "同一维度上的取舍"}
  ],
  "gap_derivation": [
    {"gap": "空白表述", "basis": "common_limitation|uncovered_scenario|conflict",
     "evidence_anchors": ["S3"], "filled_by": "本文方法如何填补"}
  ],
  "contribution_map": [
    {"contribution": "贡献条目", "evidence": "method§2|fig3|table1",
     "scope": "适用边界，不夸大"}
  ]
}
```

写作/核对时的分组与空白推导模板（步骤 3、4 使用）：

- 分组维度三选一或组合：**问题设定**（解决什么问题）、**方法路线**（技术路线/模型/算法）、**数据与评价方式**（数据集、指标、实验设置）。同一组内沿该维度比较取舍，不按时间罗列。
- 空白推导三选一：**共同限制**（所有已引工作的同一局限）、**未覆盖场景**（文献未处理的设定/数据/条件）、**冲突结论**（不同工作结论不一致）。推导不出时标“作者判断”，不得冒充文献推出。

## 7. 维护信息

- 适用：中英文科研论文引言与相关工作的起草、改写、补引用、核对、综述式罗列改写为分组比较。
- 支持引用格式：案例指定格式优先；缺省作者—年份，可改数字式（改动记 change_log）。
- 限制：不做期刊排版模板适配；不生成材料外事实；不自动下载或联网补参考文献。
- 依赖：仅 Python 3.9+ 标准库，无第三方包。
- 确定性：所有 JSON 以 `sort_keys=True` 写出，无墙钟时间戳，重复运行字节一致。
