# 翻译与双语写作规则（translation_rules.md）

本文是 `scholar-bilingual-translator` 的规则手册。运行 Skill 时按以下规则处理四个核心问题；每条规则都可被 `scripts/check_consistency` 的对应检查项复核。

## 1. 术语稳（terminology consistency）

- 先抽取再翻译：翻译前先从原文抽取领域术语、缩写、上下位词，建立 `glossary.json`（模板见下）。同一术语在标题、摘要、正文、图表题注、附录使用同一译法。
- 一词一域一译：同一英文词在不同学科下译法不同（如 `resolution` 在光学/成像/决议），按案例学科锁定译法，在 `evidence_map.terms[].domain` 标注学科依据。
- 缩写在首次出现时给出全称，此后保持缩写或全称的一致用法；不允许同一缩写对应多个中文全称。
- 术语条目必须带 `source_locator` 与逐字 `source_quote`（术语首次出现的原文片段），译文侧记录 `translation` 与 `target_text`。`check` 会扫描交付文本，发现非 canonical 的 variant 即报 warning。

### glossary.json 模板

```json
{
  "terms": [
    {
      "term": "resolution",
      "canonical": "分辨率",
      "variants": ["解析度", "分辨能力"],
      "domain": "光学成像",
      "source_locator": {"file": "paper.pdf", "page": 2},
      "source_quote": "spatial resolution of 0.5 nm"
    }
  ]
}
```

规则：`canonical` 为全文统一译法；`variants` 列出可能出现但应避免的异译。正文若出现 variant，`check` 报 warning，须统一或显式说明保留原因。

## 2. 数字实（number and unit fidelity）

- 数字、单位、量级、范围、年份、样本量、统计口径必须原样落地：不四舍五入、不换单位、不"顺手取整"。
- 有效数字保持一致：原文 `95.2%` 不得写成 `95%` 或 `95.20%`；`p = 0.047` 不得写成 `p < 0.05`。
- 中英数量级表达换算必须显式：`million`/`billion` 与 万/亿 之间转换时在 `evidence_map.numbers[]` 的 `note` 写明换算关系（如 500 million = 5 亿），不得静默换算导致量级错位。
- 范围、±、约（~、about、approximately）等修饰保持原样；区间端点不得舍入。
- 公式符号、下标、上标、希腊字母、变量名不改写；若 LaTeX 无法渲染则保留原 LaTeX 源码并登记 `uncertainty`。
- 数字条目带 `source_locator` + `source_quote`，`check` 对 `segments[]` 逐段比对：原文数字在译文中缺失且无万/亿等换算提示时报 error，出现换算关键词时报警告要求人工核验。

## 3. 对得齐（segment alignment）

- 句段一一对应：翻译以句段为单位，`evidence_map.segments[]` 每条含 `id`、`source_quote`、`target_text` 及双侧 locator。段落顺序、图表编号、题注、脚注、参考文献条目保持在原位。
- 公式、表格、题注、脚注不合并、不拆分、不换序；未翻译的结构性元素（公式、图表本体）以占位符保留并在 `note` 标注。
- 双语对照交付时，同一 `id` 的源句与译句成对出现；`check` 统计源句数与译句数，不等即报 `segment_count_mismatch`。
- 分段策略：超长文档按章节/段落切分，段落边界写入 `alignment[]`；切分后重新校验编号与交叉引用，不得因切分丢失首尾内容。

## 4. 边界清（semantic boundary control）

- 增译/省译显式登记：因语法需要的增补或为通顺而省略的成分，在 `evidence_map.segments[].note` 写明，不得改变科学含义。
- 守住否定范围：`not`、`no`、`none`、`never`、`without`、`neither...nor` 的辖域不得移动；双重否定不得简化为肯定。
- 守住因果与证据强度：`may`、`might`、`could`、`suggests`、`indicates`、`demonstrates`、`proves`、`causes`、`correlates with` 的强度不得升级或降级；`likely`、`possibly`、`potentially` 等模糊限制语不得删除。
- 引用与归属完整：作者—年份、编号、`et al.`、上标、方括号格式差异按目标语习惯处理但信息不丢；`check` 逐段比对 `[...]` 编号引用是否保留。找不到参考文献原文时，标注"存疑引用"，不臆造作者、年份或 DOI。
- 不出现作者没说过的结论：译文不添加解释性补充、不"帮作者圆话"；不确定时写 `pending_human`，不擅自改写。

## 5. evidence_map 结构与约束

`result.json` 的 `evidence_map` 含五个子列表；每一条都必须带 `source_locator` 与逐字 `source_quote`（`scripts/main.py add-evidence` 会强制校验非空，缺任一即拒绝写入）：

| 子列表 | 条目关键字段 | 用途 |
|-|-|-|
| `segments` | `id, source_locator, source_quote, target_locator, target_text, note` | 句段级双语对齐，可逐句追溯 |
| `terms` | `term, canonical, translation, variants, domain, source_locator, source_quote` | 术语依据与锁定译法 |
| `numbers` | `number, unit, source_locator, source_quote, target_text, verified, note` | 数字/单位核对结果 |
| `citations` | `citation, source_locator, source_quote, target_text, preserved, note` | 引用保留与格式差异 |
| `alignment` | `id, source_boundary, target_boundary, note` | 分段边界与结构对齐记录 |

`locator` 可以是字符串或对象 `{file, page, para, line, segment}`；`quote` 必须是从材料中逐字复制的原文片段，不得改写或概括。`fact_source_map` 是 `evidence_map` 的粗粒度镜像，供平台直接读取。

## 6. 版本与边界记录

- `version_boundary.modifications[]`：每项 `{what, why, before_after}`，记录"改了什么、为什么改、改前改后"；局部修改只动该处，不扩大波及范围。
- `version_boundary.human_confirm[]`：无法自动判定、需人工确认的项。
- `version_boundary.material_gaps[]`：材料缺失、无法判断的缺口。
- `version_boundary.anomalies[]`：运行异常、渲染失败、超长截断等事件及时间。
- `version_boundary.editable`：布尔，译文/双语稿为可编辑文本时置 `true`。
