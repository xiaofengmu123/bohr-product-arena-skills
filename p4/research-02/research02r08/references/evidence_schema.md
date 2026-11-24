# 证据行字段、强度措辞与定位规范

这是 `evidence_table.jsonl` 与综述写作的字段规范。校验器 `validate_result.py` 按此逐条核查；写作时偏离本规范会导致自检失败。

## 1. 证据行字段（evidence_table.jsonl 每行一个 JSON 对象）

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `evidence_id` | string | 是 | 全局唯一，如 `S1`、`S2`；正文 `[S#]` 指向它 |
| `source_id` | string | 是 | 必须等于 `work/inventory.json` 中的 `source_id` |
| `locator` | string | 是 | 源内定位，格式见下 |
| `quote` | string | 是 | 逐字原文摘录，必须是源文本的真实子串 |
| `claim` | string | 是 | 该证据支撑的一句实质论断 |
| `strength` | string | 是 | 四选一，见下表 |
| `field` | string | 是 | 比较字段（population/method/sample_size/outcome/conclusion/limitation/not_reported 等） |
| `notes` | string | 否 | 说明样本量、方法局限、冲突标记等 |

`quote` 铁律：**逐字**。校验器会把 `quote` 归一化（折叠空白）后作为源文本子串反查；查不到即判“疑似伪造”。不得改写、拼接、把摘要当全文、把 A 文数值填进 B 文。

## 2. locator 格式

`<source_id>#<单元>:<编号>`，单元取 `page`、`para` 或 `section`。示例：

- `src_01#page:3` —— PDF 第 3 页（按 pdftotext 的换页符计数）
- `src_02#para:4` —— 题录/文本第 4 段
- `src_03#section:Methods` —— 按章节标题定位

规范化规则：页码以 `pdftotext` 输出里的 `\f` 为分页；段落以空行分隔。定位必须能让人独立回到材料原文位置，不能只写文件名。

## 3. 证据强度四级 + 措辞映射

| strength | 判定依据 | 综述措辞 | 示例动词 |
|---|---|---|---|
| `established` | 多源一致且方法充分 | 表明 / 证实 | “多项 RCT 表明……” |
| `suggestive` | 单源或方法有限（含仅摘要） | 提示 / 初步表明 | “单中心数据提示……” |
| `disputed` | 存在相反结论或互斥方法 | 存在争议 | “A 与 B 结论相反，存在争议” |
| `not_reported` | 材料未报告该字段 | 未见报告 | “长期结局未见报告” |

**“未报告”≠“不存在”**：`not_reported` 只说明本批材料没有该证据，不据此反推“无效应/无风险”。

## 4. 冲突与空白记录（conflicts_and_gaps.md）

- 冲突条目：`claim_a` + 其 `evidence_id`、`claim_b` + 其 `evidence_id`、`type`（`result_conflict` 或 `method_mutual_exclusion`）、当前可判断性。并列呈现，不静默择一。
- 空白条目：`gap_id` + 具体问题 + `evidence_absence`（检索/通读后哪些 source 未覆盖）+ 边界声明。空白要落到具体证据缺口，禁止“未来研究应关注”式套话。
- 同一指标两源不一致：都记录并标 `source_conflict`，不择一。

## 5. 筛选标准先于写作

`screening_log.json` 的 `screening_criteria` 必须包含四要素：**研究对象**（population）、**纳入条件**（include）、**排除条件**（exclude）、**去重规则**（dedup_rule）。该标准写在所有 `decisions` 之前，且每条决定可回溯到标准中的具体条款；去重由 `dedup_records` 说明“删了谁、因何删、保留了谁”。
