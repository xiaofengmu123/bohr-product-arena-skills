# 保真规则（preservation rules）

本文档是 `scripts/checks.py` 的语义说明，也是 Agent 逐句编辑时必须遵守的硬约束。
**润色只改表达，不改事实与强度。** 下列要素必须原样守恒，否则判为 error。

## 1. 必须守恒的要素（硬约束）

| 类别 | 示例 | 校验方式 |
|---|---|---|
| 数字与精度 | `3.5`、`3.50`、`1,000`、`1.2e-3`、`-0.4` | 归一化为数值后比较多重集；`3.50` 与 `3.5` 视为同值 |
| 单位与量 | `3.5 mg/kg`、`12%`、`37 °C`、`2.5 × 10⁻³` | 数字后紧跟单位构成一个 quantity 整体比较 |
| 引用编号 | `[12]`、`[12,13]`、`[12-14]`、`[12–14]` | 归一化为引用集合；不因空格/顺序改变而误报 |
| 图表指向 | `Fig. 1`、`Figure 1A`、`Table 2`、`图 1`、`表 2` | 解析为 (种类, 编号, 后缀) 比较 |
| 限定词强度 | `可能`、`may`、`suggest`、`初步`、`有望`、`尚不清楚` | 见第 2 节；缺失即 hedge_drift 警告 |
| 因果与相关 | `导致` vs `相关`、`cause` vs `associated with` | 人工判定 + 逐句理由；脚本对 hedge 词做缺失提醒 |
| 否定范围与条件句 | `not ... if ...`、`仅当`、`除非` | 保留否定与条件连接词；脚本不自动改 |
| 量词 | `所有`/`all`、`部分`/`some`、`多数`/`most` | 不静默替换量词 |

## 2. 限定强度守恒（hedge conservation）

- 每个 edit unit 记录 `hedge_justified`（bool）与 `uncertainty_reason`。
- `checks.py` 对原文出现的 hedge 词做**逐条比对**：原文有而改文没有，且 unit 未标记 `hedge_justified=true` 或 `reason`/`uncertainty_reason` 未含「限定/强度/hedge」字样 → 产生 `hedge_drift` 警告。
- 处理方式只有两种，二选一：
  1. **回退**：把限定词放回改文；
  2. **写明理由**：在 `uncertainty_reason` 说明为何该处强度可调整（例如「原文为绝对化表述，改为与数据支持的 may 表述，理由：…」），并置 `hedge_justified=true`。
- 禁止静默加强/削弱结论；脚本不自动批准，只负责提醒并留痕。

## 3. 引用与图表指向

- 引用编号、图表编号**不允许重新编号或增删**。若稿件上下文要求重新编号，必须把原编号→新编号的映射写进 `version_boundary`，并在 `pending_human` 标注「需作者确认重新编号」。
- 图/表首次出现缩写（如 `Fig.` vs `Figure`）属格式问题，可统一，但**编号与子图字母不得改动**。
- 材料中出现的文献/图表，若无法在底稿内定位到出处，写 `MATERIAL GAP`，不得凭记忆补引用、DOI 或页码。

## 4. 证据溯源（fact_source_map）

- 每个带数字、引用、图表指向或关键论断的句子，必须有一条 `facts[]` 记录：
  ```json
  {"fact": "3.5 mg/kg dose", "locator": "materials/paper.md#L12",
   "quote": "3.5 mg/kg dose [12] (Fig. 1)", "certainty": "verified"}
  ```
- `locator` 使用 `文件名#段落/行号` 或 `文件名#L<n>`；`quote` 必须是材料中的**逐字片段**（含该数字/引用/图表指向）。
- `checks.py` 的 coverage 检查会确认：原文出现的每个数字/引用/图表指向 token，都能在某个 fact 的 `quote` 或 `fact` 中作为子串找到；找不到 → `fact_missing_quote` error。
- `certainty` 取值：`verified`（逐字核对）、`paraphrased`（改述但同义）、`inferred`（推断）、`unverified`（未核对）。后两者会被计入 `uncertainty`。

## 5. 术语一致性（glossary）

- `edits.json` 的 `glossary[]` 每条含 `term / canonical / variants / first_use_locator`。
- 同一概念全文只用 `canonical` 写法；`variants` 记录原文中出现的异形写法。
- `checks.py` 对 `variants` 做漂移检查：若改文中仍残留已登记的 variant → `term_drift` 警告，提醒统一到 canonical 并更新首次出现定位。
- 缩写首次出现必须给出全称；缩写与全称的对应写进 glossary，避免跨章节漂移。

## 6. 校验器的边界（诚实的 not_reported）

脚本是**确定性、保守**的，以下情况不自动判 error，改由人工/Agent 记录到 `uncertainty` 或 `pending_human`，避免误报：

- 作者—年份引用（`(Smith et al., 2020)`）、化学式、含连接号的范围（`3–5`）、未列入 `UNIT_PATTERN` 的单位；
- 语义级等价改述（同义词替换、语态转换），脚本无法判语义，只检查硬 token 是否守恒；
- 多语种混排（中日英）中脚本未覆盖的限定表达。

这些不是「通过」，而是「脚本未覆盖」，必须显式标注 `not_reported` / `pending_human`，不得当作已核对。

## 7. edits.json 工作态 schema

```json
{
  "case_id": "...",
  "draft": {"path": "...", "bytes": 0, "sha": "..."},
  "target_lang": "zh|en", "genre": "...", "audience": "...",
  "units": [{
    "id": "u0001", "locator": "draft.md#L1",
    "original": "...", "revised": "...",
    "edit_type": "grammar|terminology|register|preservation|bilingual",
    "reason": "...", "evidence": "...",
    "confidence": "high|medium|low",
    "uncertainty_reason": null, "hedge_justified": false
  }],
  "facts": [], "glossary": [], "gaps": []
}
```

- `gaps[]` 的 `type` 取值：`MATERIAL_GAP`、`source_conflict`、`pending_human`、`not_reported`。
- 冲突信息不选边：两个来源不一致时**两条都记**并标 `source_conflict`。

## 8. 一次改坏与改对的对照（示例）

**原文**：`The observed effect may be associated with the 3.5 mg/kg dose [12] (Fig. 1).`

**改坏（被整段重写后限定词消失）**：
`The 3.5 mg/kg dose caused the effect [12] (Fig. 1).`
问题：`may be associated with`（相关、不确定）被静默改成 `caused`（因果、确定），
违反「因果与相关不得混淆」与「限定强度守恒」。

**改对（最小编辑，只改表达）**：
`The observed effect may be associated with a 3.5 mg/kg dose [12] (Fig. 1).`
（仅改冠词，保留 `may be associated with`、数字、单位、引用、图表指向。）

脚本如何兜底：`checks.py` 提取原文 hedge `may` 与 `associated`，发现改文缺失 → 产生
`hedge_drift` 警告；数字 `3.5`、单位 `mg/kg`、引用 `[12]`、图表 `Fig. 1` 均已守恒，
无 error。Agent 收到 hedge 警告后二选一：回退，或写 `hedge_justified=true` 并给理由。

## 9. 校验器覆盖范围自查

| 要素 | 脚本自动 | 需人工/标注 |
|---|---|---|
| 数字、单位、`[n]` 引用、图表指向守恒 | ✅ error 级 | — |
| 每个 token 有 locator+quote | ✅ error 级 | — |
| fact 有 locator 与 quote | ✅ error 级 | — |
| hedge 词缺失提醒 | ✅ warning 级 | 回退或写理由 |
| glossary variant 残留 | ✅ warning 级 | 统一到 canonical |
| 因果/相关、否定范围、量词语义 | — | 逐句理由 + uncertainty |
| 作者—年份引用、化学式、未列单位 | — | `not_reported`/`pending_human` |

## 10. 语域与文体（genre register）

不同体裁的语气要求不同，但都属于"表达"层面的可调整项，不得借语域调整改写事实。

| 体裁 | 语气要点 | 常见必改 | 常见不可改 |
|---|---|---|---|
| 期刊论文 | 客观、第三人称、信息密度高 | 口语词、绝对化表述、悬垂修饰 | 结论强度、数据、引用 |
| 学位论文 | 结构完整、解释充分、允许适度铺垫 | 冗余口语、逻辑断裂 | 作者主张、限定词 |
| 基金申请书 | 目标导向、强调可行性与预期 | 宣传腔、无依据的"颠覆性" | 预算/里程碑数字 |
| 审稿回复 | 礼貌、逐点回应、不回避 | 攻击性措辞、答非所问 | 审稿人原话引用、修改定位 |
| 英文摘要 | 时态规范、单句承载完整信息 | 中式英语、直译腔、冗余名词化 | 数字、单位、因果与相关 |

- 术语译法遵循领域惯例，无法确定时写 `pending_human` 或保留原文并标 `not_reported`。
- 缩写在首次出现处给全称；大小写与连字符统一到 glossary 的 canonical 写法。

## 11. 可复用性与维护

- 本文档 + `checks.py` + `uncertainty-rules.md` 可直接被他人用于自己的稿件：把
  `edits.json` 当作输入，`checks.py.build_report` 即返回守恒报告，不依赖本 Skill 的
  其它部分。
- 扩展学科术语：向 `checks.py` 的 `HEDGE_ZH`/`HEDGE_EN`/`UNIT_PATTERN` 追加该学科的
  限定词与单位即可；追加须保持确定性（排序、无随机）。
- 自定义风格：把目标期刊/基金口的风格规则写进本文件第 10 节的表格，与 `edits.json`
  的 `genre` 字段对齐，Agent 据此注入风格而不改事实。
- 维护原则：改动校验逻辑后先跑 `python3 -B scripts/selftest.py`；改动只允许收紧
  或新增检查，不允许放宽「不编造、不静默改强度」两条铁律。
- 版本：`version` 跟随 SKILL.md frontmatter；`change_log` 记录每次 scaffold/finalize
  的行为，不记录任何编造的评测分数。
