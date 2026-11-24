# 方法规则（references/method.md）

执行检索时按下列规则操作；每条规则都要能被第三方按 result.json 复核。

## 1. 术语对齐（terminology_table）

对研究问题中的每个核心概念建立术语表条目，字段：`term`（源词）、`language`、`variant`（同义词/缩写/上下位/音译/本地译名）、`type`、`basis`（依据：词典、材料原文、出版方术语）、`verification`（如何复核）。

- 只收录有依据的变体；无法确认的译法标 `unverified_translation`。
- 控制发散：每个源词的同义扩展优先走“学科用词”，不收录纯字面同义词。
- 缩写冲突必须说明：同一缩写在目标语种可能指向不同概念时，两个都记录并标 `ambiguous_abbrev`。

## 2. 逐语种查询（queries）

- 每个目标语种至少一条查询；查询由“术语表组合”生成，不是整句机翻。
- 记录：`query`、`language`、`translation_service` 与 `version`、`original_query`（源语对照）。
- 未记录翻译服务与版本的译文不得当作已核对。
- 中文检索可加繁体/简化双写；日文可加片假名转写；德文保留本地复合词与词形变化。

## 3. 检索与筛选（results）

- 用 web_search / paper_search 逐语种检索，不得只用母语。
- 每条命中记录：`title`、`authors`、`year`、`venue`、`doi_or_url`、`language`、`version`、`source`、`relevance_basis`。
- 排序/筛选依据单独记录（`criteria` 或 results 内 `rank_reason`），不与结果混合。

## 4. 语言与版本判定

- 语言判定写依据：正文语言、摘要语言、双语刊声明、出版方翻译声明；脚本识别只作提示（`script_only`），不单独决定语种。
- 版本取值：`original`（原文）、`translation`（出版方/期刊正式译文）、`bilingual`（双语刊同刊同文）、`abstract_only`（仅摘要）、`undetermined`（不可判定）。
- 判定不了就写 `undetermined` 并记入 `uncertainty`，不冒充全文。

## 5. 去重（dedup）

同一论文的多语版本去重后计数。判定依据写清：DOI 相同、标题翻译对照、作者/期刊/年份一致、出版方标注为同一论文的译文。只凭标题相似不去重。

## 6. 误译复核与证据对齐（evidence_map / conflicts）

- 对高风险命中做回译或人工抽检，识别“词面对应、语义不同”的假阳性，记判定依据。
- 每个关键结论/数值/命中给 `locator`（URL/DOI/材料段落定位）+ `quote`（逐字引用）。
- 冲突信息不隐藏：同指标两个来源不一致时都记录并标 `source_conflict`。
- 缺失元数据、无结果、权限问题逐条写入 `missing`；不补造。

## 7. 增量更新协议

- 第 0 步 `init` 先落骨架；其后每步用 `patch` 增量写回，不要整体重写 result.json。
- `patch` 深合并规则：字典递归合并、列表整体替换、标量替换；顶层 `_note` 自动追加 change_log。
- 收尾跑 `validate` 确认契约字段齐全、JSON 合法。

## 8. 结果条目 schema（逐字段）

`results` 每条按下列键填写，缺值用 `null`，不删键：

| 键 | 含义 | 必填 |
|---|---|---|
| `title` | 文献标题（按命中语种原样） | 是 |
| `authors` | 作者列表 | 否（缺写 `null`） |
| `year` | 出版年 | 否 |
| `venue` | 期刊/会议/出版方 | 否 |
| `doi_or_url` | DOI 或原始 URL | 是（不可得写 `not_reported`） |
| `language` | 文献语言（`undetermined` 允许） | 是 |
| `version` | original/translation/bilingual/abstract_only/undetermined | 是 |
| `source` | 检索来源（web_search/paper_search/材料） | 是 |
| `locator` | 来源定位（URL/DOI/材料段落） | 是 |
| `quote` | 逐字证据 | 是（缺失标 `pending_human`） |
| `relevance_basis` | 相关性依据 | 是 |

## 9. 语言/版本判定决策表

| 证据 | 版本判定 |
|---|---|
| 出版方标注为原文，正文与元数据同语种 | `original` |
| 出版方/期刊明确声明为另一语种的正式译文 | `translation` |
| 双语刊同刊同文（两种语言并列刊出） | `bilingual` |
| 只能读到摘要，无法确认全文语言 | `abstract_only` |
| 语言或版本证据不足 | `undetermined`（并写入 `uncertainty`） |

脚本识别只作提示（`detect_script` → `script_only`），最终语种须由出版方/正文/摘要证据确认；两者不一致时以正文/出版方为准并记录。

## 10. 术语表条目示例（示例，非正式答案）

```json
{"term":"细胞焦亡","language":"zh","variant":["pyroptosis"],
 "type":"standard","basis":"材料原文/权威词典",
 "verification":"以英文学科用语检索并比对摘要原文"}
```

未验证的译法示例：`{"term":"…","variant":["…"],"type":"candidate","basis":null,"verification":"pending_human"}`，并标 `unverified_translation`。

