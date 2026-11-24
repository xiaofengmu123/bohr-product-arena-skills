# 基金申请书写作 Skill — 规则与证据溯源规范

本文件是 `SKILL.md` 的执行细则，规定三类硬规则：**不编造**、**证据溯源**、
**四问一致**。运行期 Agent 必须逐条遵守；与本文冲突时以本文为准（除 case.json
显式 output_contract 外）。

## 1. 不编造红线（最高优先级）

- 不新增材料之外的事实、数字、引用、DOI、卷期页码、图表内容或政策口径。
- 不把"已完成"写成"拟开展"，反之亦然；材料未明确区分的，一律标
  `pending_human`（需人工确认）或 `not_reported`（材料未报告）。
- 不预写正式测试答案、不生成白板、不编造 benchmark 分数或运行结果。
- 缺材料/缺软件时如实写 `status=partial` 或 `insufficient_evidence`，并在
  `version_boundary.material_gaps` 与 `uncertainty` 逐条说明，绝不补造。
- 文件名、领域标签、目录名都不是事实依据；必须读实际文件内容。

## 2. 证据溯源规范（本 Skill 变体核心）

每一条进入申请书的**关键结论**（数字、术语、引用、图表、政策口径、前期成果
论断）都必须登记到 `fact_source_map.claims`，且每条含五项：

| 字段 | 含义 | 硬要求 |
|---|---|---|
| `claim_id` | 结论编号，如 `C01` | 唯一、稳定，全文引用 |
| `claim_text` | 结论/论断的完整表述 | 与稿件正文逐字一致 |
| `kind` | fact / number / term / citation / figure / policy / inference | 只能取其一 |
| `locator` | `相对路径#页码§段落/表/图`，如 `materials/paper.pdf#p3§2` | 必须指回真实文件定位 |
| `quote` | 材料中的**逐字原文**片段 | 必须可反查，禁止改写/概括冒充原文 |
| `source_ref` | 来源文件相对路径 | 与 `sources` 清单一致 |
| `status` | mapped / unmapped / conflict / pending_human / not_reported | 见第 3 节 |

要求：

1. **每条 mapped 结论都必须有非空 `locator` 与逐字 `quote`**；`selftest.py`
   会把"有 locator 无 quote"或"有 quote 无 locator"判为 FAIL。
2. `quote` 必须是原文逐字片段。若材料只有摘要、OCR 错乱或页眉遮挡，导致无法
   取得逐字原文，则该条降级为 `unmapped` 并在 `note` 说明"仅有摘要/OCR 无法
   逐字引用"，不得自行补全原文。
3. **推断**（`kind=inference`）必须单独标注，`note` 写清推理链与前提；推断
   结论在正文中只能作为推断表述（如"据此可推断""初步表明"），不能当作材料
   事实（"表明""证实"）。

### 2.1 三级可复核对应（段落—要素—证据）

`fact_source_map.trace` 维护两条映射，保证第三方能判断"某句话该不该这么写"：

```json
{
  "artifact_to_element": {"manuscript.md#L12-L15": "创新点2"},
  "element_to_claims": {"创新点2": ["C07", "C08"]}
}
```

- 稿件段落 → 七要素：`artifact_to_element`；
- 七要素 → 结论编号：`element_to_claims`；
- 结论编号 → 材料定位：`claims[].locator + quote`。

三步闭环都通，才允许把该段落视为"有据"；任一步断链，必须在
`version_boundary.manual_confirm` 登记"该段落暂缺证据链，需人工确认"。

## 3. 状态机与冲突处置

| status | 含义 | 允许出现在正文？ |
|---|---|---|
| `mapped` | 有定位 + 逐字原文 | 是，按事实表述 |
| `unmapped` | 结论来自材料但未能逐字定位 | 仅可弱化表述，且段落标注待核 |
| `conflict` | 两个来源不一致 | 双方依据都记录，正文不得擅自选一 |
| `pending_human` | 需人工确认（如申请人真实身份、政策是否现行） | 正文标注"待确认"，不写成既定事实 |
| `not_reported` | 材料未提供该信息 | 不得出现在正文；只在对照表标"材料未报告" |

**冲突规则**：同一指标两个来源不一致时，`fact_source_map.conflicts` 记录
`claim_id`，`note` 同时列出双方 `locator + quote`；正文只能写成"材料 A 记 X，
材料 B 记 Y"的并列表述，或交给 `pending_human`，**不得自行挑选其一、不得自行
改分**。这与双评分分歧同理：分歧超过一档或参考答案缺失时，记录双方依据与
判据，不擅自改分。

## 4. 四问一致（对应题面"立得住/说得准/对得上/查得到"）

写作/自检时把每个要素翻译成**可观察判据**，逐条勾选，禁止"准确/完整"这类
空泛表述：

### 4.1 立得住（立项依据的必要性）
- [ ] 逐句区分"领域现状（别人做到哪一步，引谁）"与"关键瓶颈（还差什么）"。
- [ ] 必要性论证有 `locator+quote` 支撑，而非政策口号堆叠。
- [ ] "为什么是我们"对应到 `pending_human` 或前期成果证据链，不空喊。

### 4.2 说得准（创新点可核验）
- [ ] 每个创新点都点名具体已有工作（作者/年份/方法）并写清差别。
- [ ] 禁用"首次/国际领先/填补空白"等无据修饰；出现即 FAIL。
- [ ] 每条差别有对比依据（`conflict` 或两侧 `locator+quote`）。

### 4.3 对得上（目标—内容—路线—指标一致）
- [ ] 研究目标、研究内容、技术路线、考核指标的条目数量与编号逐条对应。
- [ ] 用 `artifact_to_element` 与 `element_to_claims` 核对"目标 3 个、路线 2 条、
      指标另说一套"类断裂；断裂处登记 `manual_confirm`。

### 4.4 查得到（来源可追溯、已完成/拟开展分清）
- [ ] 引用、数据、前期成果、政策口径每条都有 `locator+quote`。
- [ ] 正文明确用"本团队已完成/前期结果"vs"拟开展/拟采用"区分时态。

## 5. 降级与异常（与 SKILL.md 第 6 节一致）

| 场景 | 处置 |
|---|---|
| 缺数据/缺材料 | `version_boundary.material_gaps` + `uncertainty`，`status=partial` |
| PDF/OCR/LaTeX/Office 渲染不可用 | `uncertainty` 写明"未生成渲染预览"，保留 Markdown/纯文本可编辑稿，不伪造预览 |
| 材料超长/乱码 | 分段读取并记录已读范围；未读部分进 `material_gaps`，不得声称已全部核对 |
| 模型拒答/截断 | 分块处理，已完成块先落盘，续写只改差异 |
| 空输入目录 | `status=insufficient_evidence`，退出码 0，写合法 result.json |
| 坏 JSON/坏编码 | 记入 `errors`/`anomalies`，继续处理其余材料 |

## 6. 材料清单与快照

- `python scripts/main.py` 与 `python scripts/evidence.py snapshot --input-dir /input`
  会生成 `sources`（相对路径 + 字节大小 + SHA256）。引用任何来源前，先确认其
  SHA256 出现在快照中；SHA256 缺失或为空的来源不得作为 mapped 证据。
- 快照须**确定性**：文件按相对路径排序、JSON `sort_keys=True`，两次运行同一
  输入应得到同一 `sources` 列表。
