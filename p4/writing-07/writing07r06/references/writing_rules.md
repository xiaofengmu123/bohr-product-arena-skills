# 写作与证据规则（引言与相关工作）

本文件是 SKILL.md 第 6 节的完整展开。原则一句话：**正文里每一个可被质疑的断言，都要能沿 [S#] → evidence_map → plan.json → 来源材料 找到逐字证据；找不到证据的，就明说找不到，不补、不猜、不伪造。**

## 1. 四段的功能与承接

### 1.1 研究背景（立得住）

- 目标不是"说重要"，而是交代一条具体的研究脉络：领域当前解决某问题的**技术约束**是什么、为什么既有办法到这里就推不动了。
- 每一句背景事实要么挂来源 `[S#]`，要么是后面相关工作/空白推导所需的公共前提；泛化的"近年来受到广泛关注""已成为研究热点"一律删除。
- 结尾落到一个**具体未解决问题**，且该问题在 plan.json `background.target_gap` 中可查，与 `gap.derivation` 呼应。

### 1.2 相关工作（分得清）

- **先分组、后比较**：按问题设定 / 方法路线 / 技术路线分组，组内按同一维度（问题设定、方法、数据、评价方式）比较取舍，不按时间顺序罗列。
- 分组方案先写进 plan.json `related_work.grouping_scheme`，正文与之一致；评审先看分组依据是否成立，再看正文是否落实。
- 转述他人工作必须保留：原始结论、适用条件、局限。禁止把"在 X 数据集上提升 3 个点"改写成"大幅超越"；禁止省略原文的限制条件。
- 每一处转述挂引用标记（如 `[1]`），引用落在具体论断上，而不是段尾一堆编号。

### 1.3 研究空白（说得实）

- 空白必须由**已引文献**的缺口推出，缺口来源三选一或多选：
  1. 共同限制：多篇工作共享同一假设/数据集/评测，未覆盖某场景；
  2. 未覆盖场景：已引工作明示或默示不适用的条件；
  3. 冲突结论：两篇结论在相同条件下不一致。
- 推导链写进 plan.json `gap.derivation` 与 `gap.basis`（指向具体文献）；正文只需陈述结论，但评审可回溯依据。
- 本文如何填补必须写明：对应本文方法/实验的哪一部分；"填补"不是空口号，要有正文可指认。
- 无法由文献直接推出的判断，必须显式标 **"作者判断"**，与有据断言分开。

### 1.4 贡献（对得上）

- 把方法、实验、结论压缩成**可核对**的贡献条目，每条在 plan.json 中有 `{claim, evidence, scope}`。
- `evidence` 指向本文的方法节、图、表、结果段落；写不出 `evidence` 的贡献条目不成立，应删除或降级。
- `scope` 限定适用范围，不夸大：数据集/条件/指标是什么就写什么。

## 2. 证据溯源（硬要求）

### 2.1 evidence_map 格式

`evidence_map.json` 是 `[S#]` → `{locator, quote, kind}` 的映射：

- `locator`：段落级定位，如 `materials/paper.md#p3`（第 3 段）或 `notes.md#L12`；PDF 用平台解析出的段落/页定位。
- `quote`：来源材料的**逐字片段**，不是改写后的复述；宁可短，不可改字。
- `kind`：`fact | number | citation | claim | method | result` 之一。

正文事实句写作 `[S#]` 标记；一个 `[S#]` 对应 evidence_map 一条，可多句共用一个来源，但每条来源都必须逐字可查。

### 2.2 result.json 的 sources

`sources` 是 evidence_map 的摘要镜像：每项 `{key, locator, quote, kind}`，`key` 用 `S#` 或材料名编号。二者必须一致，评审可以只打开 result.json 抽查，再回 evidence_map 全量核对。

### 2.3 冲突与未知

- 同一指标两处材料不一致：两条都记入 `sources`，并在 `uncertainty` 标 `source_conflict`，正文采用其一时必须说明取舍理由（记 change_log）。
- 数字/单位/统计量逐字复用：`91.2%` 不得写成 `91%`，`±0.3` 不得省略；材料没有的不补，写 `MATERIAL GAP` 或 `not_reported`。
- 引用元数据（作者/年份/DOI/页码）**只用材料中真实出现的**；查不到原文的引用列 `pending_human`，不臆造。

## 3. 引用处理

- 案例指定引用格式时优先遵循；未指定时缺省作者—年份（如 `Smith et al., 2024`），可改数字式，但改动记 change_log，并在 `uncertainty` 声明该假设。
- 关键工作、代表性工作、近期进展都要覆盖，缺哪一类在 `uncertainty` 写明（如"近期 2025 年之后的进展未在材料中出现"）。
- 引用编号/键名与 evidence_map 一致；一个论断对应一个引用，禁止段尾堆编号或空挂引用。

## 4. 语言切换与双语

- 换语时术语、数字、引用、图表编号原位，仅换叙述语言；限定强度（"显著/表明/初步表明/有望"）不变。
- 中文成稿遵循中文书面语规范；专业术语与缩写保留原文并在首次出现处标注。
- 若案例要求中英对照，两版共享同一 evidence_map 与 change_log，避免双版漂移。

## 5. 确定性

- 无随机成分；重复运行同一输入应得相同正文与 plan.json（`runtime` 时间戳、`at` 时间戳是元数据，允许不同）。
- 若采用任何排序（分组、引用、来源），用稳定顺序（按材料出现顺序或字典序），避免两遍结果漂移。

## 6. 状态判定（诚实取值）

| status | 含义 |
|---|---|
| `ok` | 四段齐全 + 证据齐全 + 合同字段已回填 |
| `partial` | 有可用稿但存缺口（缺某段/缺证据/缺合同字段值） |
| `insufficient_evidence` | 材料不足以成稿，只能写可支撑内容 |
| `input_error` | 输入无法解析（case.json 坏/缺失、契约缺失） |

判定只反映**本次运行实际完成度**，不夸大、不自我打分；`pending_human` 列出需要人工确认的项（如引用原文缺失、期刊格式未知）。

## 7. plan.json 字段参考（中间表示规范）

`plan.json` 是"计划与成稿分离"的唯一权威源，逐字段语义如下。

| 字段 | 类型 | 语义 | 填写时机 |
|---|---|---|---|
| `case_id` | string/null | 来自 case.json，缺失为 null | 骨架 |
| `audience_and_format` | object | `audience`（读者）、`venue`（目标期刊/会议）、`language`、`citation_style`、`length_constraint` | 扫描后 |
| `sections.background` | object | `purpose`（为何现在值得做）、`target_gap`（指向的具体未解决问题）、`claims[]`（背景事实句 `[S#]` 列表） | 填计划 |
| `sections.related_work.grouping_scheme` | array | 每项 `{group, criterion, works[], comparison}` | 填计划 |
| `sections.related_work.comparison_dimensions` | array | 组内比较维度，缺省 `["problem_setting","method","data","evaluation"]` | 填计划 |
| `sections.gap` | object | `derivation`（推导叙述）、`basis[]`（依据文献编号）、`author_judgment[]`（标"作者判断"的项） | 填计划 |
| `sections.contributions` | array | 每项 `{claim, evidence, scope}` | 填计划 |
| `evidence_budget` | object | `facts[]`（事实 `[S#]`）、`citations[]`（引用键），成稿前先预算再分配 | 起草前 |
| `decision_log` | array | 每项 `{at, decision, reason}`，关键取舍必须记 | 全程 |

分组方案（`grouping_scheme`）三项缺一不可，否则退化为"逐篇摘要拼接"：
- `criterion`：这一组是按**问题设定**、**方法路线**还是**技术路线**聚的，一句话可复核；
- `works[]`：组内工作，按材料出现顺序；
- `comparison`：组内比较时在同一维度上说了什么取舍，例如"三篇都用合成数据，只在泛化到真实数据的程度上有别"。

## 8. 常见失败模式与本 Skill 的对策

| 失败模式 | 表现 | 对策 |
|---|---|---|
| 空转铺垫 | "近年来受到广泛关注" | background 必须落到 `target_gap` 与具体技术约束 |
| 逐篇摘要 | 相关工作按时间一篇一段 | 先写 `grouping_scheme`，组内同维度比较 |
| 空白拍脑袋 | "尚缺乏研究"但无依据 | `gap.basis` 必须指向已引文献的限制/冲突 |
| 贡献空挂 | 贡献与正文方法对不上 | 每条 `contributions[].evidence` 指向方法/图表/结果 |
| 数字漂移 | 91.2%→91%，±0.3 丢失 | facts 与 evidence_map 逐字复用，起草后逐条核对 |
| 引用堆尾 | 段尾一排编号，无法对应论断 | 引用落在具体论断上，`[S#]`/`[n]` 位置即证据 |
| 元数据臆造 | 补了材料里没有的作者/DOI | 只抄材料里出现的，缺失写 `pending_human` |
| 半途崩溃 | 异常后目录为空 | 前 3 分钟先落骨架 result.json，之后增量 |

## 9. 四层回溯示例（合成，标注示例）

假设正文有一句"该基线在公开评测集上达到 88.4% 的准确率 [S3] [2]"。评审的回溯路径：

1. **manuscript** → 找到 `[S3]` 与引用 `[2]`；
2. **evidence_map.json** → `"S3": {"locator": "materials/baseline.md#p5", "quote": "achieves 88.4% accuracy on the public benchmark", "kind": "number"}`；
3. **plan.json** → `related_work.grouping_scheme` 中该工作所属组、`gap.basis` 中是否用了这一限制；
4. **来源材料** → `materials/baseline.md` 第 5 段逐字核对 `88.4%` 与"公开评测集"。

四层任一断裂（quote 与原文不一致、引用编号对不上、分组遗漏），即为待修项；不允许用推断补断裂处，只能标 `MATERIAL GAP` 或 `pending_human`。

## 10. 可复用模板（Agent 按案例套用，写入 plan.json / work 目录）

### 10.1 相关工作组内比较矩阵（起草前逐组填写）

每组建一张表，行=组内工作，列=同一比较维度；填完即"分得清"的依据，正文按此写，不逐篇罗列。

| 工作 | 问题设定 | 方法路线 | 数据与规模 | 评价方式 | 适用条件/局限 | 与本文关系 |
|---|---|---|---|---|---|---|
| 工作 A |  |  |  |  |  |  |
| 工作 B |  |  |  |  |  |  |
| 本文 |  |  |  |  |  |  |

组名与分组依据（`criterion`）写在上方，例如"按方法路线分组：监督式 vs 自监督式"。

### 10.2 贡献—证据对照表

| 贡献条目 | 证据位置（方法/图表/结果） | 适用范围（数据集/条件/指标） | 是否作者判断 |
|---|---|---|---|
| C1 | 方法 §X / 图 Y / 表 Z |  | 否 |
| C2 |  |  | 否 |

`evidence` 写不出位置、`scope` 写不出条件，该条降级或删除。

### 10.3 空白推导三要素（`sections.gap` 填写模板）

1. **缺口来源**：共同限制 / 未覆盖场景 / 冲突结论（可多选）。
2. **依据文献**：列出编号，如"文献 [2][5][7] 均只在合成数据上评测"。
3. **本文填补**：对应本文哪一方法/实验补上，如"本文在真实标注集 X 上评测"。

三者缺一，`gap.derivation` 视为不成立；第三步写不出时该空白降级为"作者判断"。

### 10.4 evidence_map 行模板

```json
{
  "S12": {
    "locator": "materials/paper.md#p7",
    "quote": "逐字片段，宁短不改字",
    "kind": "fact | number | citation | claim | method | result"
  }
}
```

### 10.5 change_log 行模板

```json
{"at": "2026-11-24T08:02:00", "target": "manuscript.md#背景",
 "before": "原表述或 null", "after": "新表述",
 "reason": "为何改（改分组/核对数字/换语/取舍冲突来源）"}
```

数值与单位保全清单单独成节或并入 `preserved_facts.json`：`{数字, 单位, 出处, 是否改动}`，凡改动必有 change_log 对应。

### 10.6 完整 plan.json 合成示例（标明示例，非正式案例）

```json
{
  "case_id": "demo-003",
  "plan_version": "filled",
  "audience_and_format": {"audience": "ML researchers", "venue": "workshop", "language": "en", "citation_style": "author-year", "length_constraint": "<=1200 words"},
  "sections": {
    "background": {"purpose": "合成数据训练的模型在真实分布上退化", "target_gap": "缺乏在真实标注集上的评测证据", "claims": ["S1", "S2"]},
    "related_work": {
      "grouping_scheme": [
        {"group": "supervised baselines", "criterion": "method route: full supervision", "works": ["baseline-A", "baseline-B"], "comparison": "两篇均在合成数据上评测，未报告真实数据泛化"},
        {"group": "self-supervised", "criterion": "method route: pretext task", "works": ["selfsup-C"], "comparison": "仅覆盖单一领域，评测指标不同"}
      ],
      "comparison_dimensions": ["problem_setting", "method", "data", "evaluation"],
      "works": ["baseline-A", "baseline-B", "selfsup-C"]
    },
    "gap": {"derivation": "已引工作均只在合成数据上评测，共同限制为缺少真实分布评测", "basis": ["baseline-A", "baseline-B", "selfsup-C"], "author_judgment": []},
    "contributions": [{"claim": "在真实标注集 X 上复现并比较三类基线", "evidence": "实验结果表 1", "scope": "数据集 X，准确率指标"}]
  },
  "evidence_budget": {"facts": ["S1", "S2", "S3"], "citations": ["baseline-A", "baseline-B", "selfsup-C"]},
  "decision_log": [{"at": "2026-11-24T08:10:00", "decision": "相关工作按方法路线分两组", "reason": "同维度比较取舍，避免逐篇罗列"}]
}
```

示例中每条 `grouping_scheme` 都带 `criterion/works/comparison`，`gap.basis` 指向具体文献，`contributions[].evidence` 指向正文表 1——这三处齐全，正文与计划才不会漂移。
