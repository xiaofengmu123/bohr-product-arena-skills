# 证据溯源、语气强度与不确定性分类

本文件是 `SKILL.md` 中“证据、强度与不确定性”一节的可执行细则。目标是让第三方能对**每一句正文**判断“该不该这么写”：数值有出处、措辞与证据等级一致、不确定处显式标记。

## 1. 来源定位格式（locator）

统一格式，逐字 `quote` 必须能在材料里找到原文：

| 材料 | locator 示例 | 说明 |
|-|-|-|
| 数据表 | `table-2#row-3` | 表 + 行/列定位 |
| 图 | `figure-1#panel-b` | 图 + 面板；多面板必须到面板 |
| 图注/正文 | `manuscript.pdf#p7` 或 `fig1-caption` | 段落或图注定位 |
| 统计输出 | `stats_output.csv#t.test` | 统计表 + 检验项 |
| 文献 | `ref-14` 或 DOI（仅当原文可查） | 不虚构 DOI/URL |

规则：`verified`（可核实）必须同时有 locator 与逐字 quote；`unverified` 有 locator 但 quote 不可得；`not_reported` 材料未提供；`inferred` 为 Agent 推断，须在 `quote` 写推理依据而非伪造原文。

## 2. 语气强度阶梯（说得住）

正文每句的谓语/副词必须匹配证据等级，禁止向上越级：

| 等级 | 含义 | 允许措辞示例 | 禁止措辞示例 |
|-|-|-|-|
| `observed` | 直接观察事实 | 观察到、测得、为 | — |
| `statistical` | 有显著性检验支撑 | 显著升高（p=…）、差异显著 | 把 p=0.06 写成“显著” |
| `directional` | 趋势一致但未达显著 | 呈升高趋势、倾向、未见统计学差异 | 显著升高、明确增加 |
| `comparative` | 组间比较 | 高于/低于/无差异（须标注检验） | 优于、强于（未给指标） |
| `speculative` | 机制推测 | 可能、提示、推测、有待验证 | 证明、证实、揭示了 |
| `external` | 文献对照 | 与 … 一致/不一致（附文献） | 首次、填补空白 |

因果边界：相关 ≠ 因果。观察性/预测性结论不得用因果动词；机制推测必须标 `speculative` 并写入 `uncertainty`（`mechanism_speculation`/`causal_boundary`）。

## 3. 不确定性类型（写入 uncertainty）

每个不确定项含 `id / item / type / reason / resolution`：

| type | 触发条件 | 处置 |
|-|-|-|
| `missing_data` | 所需数值/统计量未提供 | 字段留空或 null，不补造 |
| `unit_ambiguous` | 单位不明 | 标注“单位不明”，不猜测换算 |
| `significance_unstated` | 未报 p 值/置信区间 | 只写方向，不写“显著” |
| `source_conflict` | 两来源同一指标不一致 | 两值都记录，标冲突 |
| `mechanism_speculation` | 解释性推断无直接证据 | 标 `speculative` |
| `causal_boundary` | 相关/预测被误作因果风险 | 显式写“不能据此推断因果” |
| `truncated` | 超长输入截断 | 说明未覆盖范围 |
| `tool_unavailable` | 渲染/解析工具缺失 | 交付纯文本并记 limitations |
| `pending_human` | 需作者确认 | 同步写入 pending_human |

## 4. 结果与讨论分界（分得清）

- 结果段：只写观察事实、方向、幅度、统计量，不含解释与文献对照。
- 讨论段：解释、机制推测、文献对照、局限。逐条对应本研究数据或具体文献。
- 禁止：结果段出现“可能是因为”、讨论段重复罗列全部数值而不给解释或对照。

## 5. 图表读数规则（读得准）

- 数值连同单位、有效数字、误差棒含义（SD/SEM/CI，须在图注或方法中确认）一起抽取；误差棒含义不明时标 `unit_ambiguous`/`significance_unstated`。
- 多面板图必须定位到面板；补充材料编号保留。
- 正文数值必须与图表一致：不一致视为 `source_conflict`，不得默默改掉。

## 6. facts_sources 条目模板（可复用）

每条关键论断/数值对应一个条目，字段如下：

```json
{
  "id": "f01",
  "claim": "正文中的关键论断或数值表述",
  "locator": "table-2#row-3",
  "quote": "材料中的逐字原文片段",
  "kind": "number | term | citation | figure | claim",
  "strength": "observed | statistical | directional | comparative | speculative | external",
  "status": "verified | unverified | inferred | not_reported"
}
```

字段含义：

| 字段 | 必填 | 说明 |
|-|-|-|
| `id` | 是 | 稳定编号，正文可用 `[f01]` 反查 |
| `claim` | 是 | 正文句的凝练表述 |
| `locator` | 是（除 not_reported） | 材料位置 |
| `quote` | `verified` 时必填 | 逐字原文，不得改写 |
| `kind` | 是 | 证据类型 |
| `strength` | 是 | 语气强度等级 |
| `status` | 是 | 核实状态 |

规则：`status=verified` 而 `quote` 为空 → `check_result.py` 报硬错误；`status=unverified`/`not_reported`/`inferred` 允许但必须留下 reason（写入 `uncertainty`）。

## 7. manuscript.md 章节骨架（模板，非示例答案）

```markdown
# {case_id 或指定标题}

## Results
<!-- claim_class: result_observation -->（只陈述观察事实，逐句可挂 [f##] 反查）

### {按图序或按问题组织的子节}
...

## Discussion
<!-- claim_class: discussion_interpretation -->（解释、文献对照、局限）

### Comparison with previous work
<!-- claim_class: comparison_to_literature -->

### Limitations
<!-- claim_class: limitation -->（方法/样本/测量/统计层面，不写泛泛自评）
```

正文每段标注 `claim_class`，机制推测只允许出现在 Discussion 且 `strength=speculative`。

## 8. 微示例：读图 → 列发现 → 写句 → 核证据（示意，非真实案例）

假设材料 figure-2 两个面板显示干预组与对照组的某指标，图注注明误差棒为 SEM、n=12，并给出 p=0.06。

**步骤 1 读图（读得准）** —— 抽取 fact：

| 字段 | 值 |
|-|-|
| 数值 | 干预组 8.4 ± 0.3，对照组 7.1 ± 0.3 |
| 单位 | mg/dL（图注未写则标 `unit_ambiguous`） |
| 误差棒 | SEM，n=12 |
| 统计量 | p=0.06 |

**步骤 2 列发现（分得清）** —— 结果段事实与讨论段解释分开：

- 结果段事实：`干预组 8.4±0.3（SEM）高于对照组 7.1±0.3（SEM），p=0.06，未达预设显著性水平。`
- 讨论段解释：`这一方向一致但未达显著的趋势可能与样本量有限（n=12）有关，属推测，有待更大样本验证。`

**步骤 3 写句（说得住）** —— 语气匹配证据：用“高于 + 未达显著”，而非“显著升高”；机制解释标 `speculative`。

**步骤 4 核证据（核得了）** —— facts_sources 条目：

```json
{"id":"f07","claim":"干预组 8.4±0.3(SEM) vs 对照 7.1±0.3(SEM), p=0.06",
 "locator":"figure-2","kind":"figure","strength":"directional","status":"verified",
 "quote":"8.4 ± 0.3 … 7.1 ± 0.3 … p = 0.06"}
```

注意：`quote` 必须是图注/正文逐字片段；若原文只说“无显著差异”而未给 p，则 `strength=directional` 并标 `significance_unstated`。
