# 方法规则（references/method.md）

本 Skill 面向**跨语言已知条目（known-item）文献检索**：研究问题已经指向一
篇或多篇可唯一识别的作品，任务是找回它、判定语种与版本、跨语种去重并保留
来源/DOI/版本/正文语种/署名归属。运行边界（来自题目）为双向跨语言、3 部作品
6 个视图：原文葡萄牙语/英语正文、出版社完整译文、仅英文摘要版本、以及精确重
复条目。本文件规定判定与记录规则，供执行 Agent 逐条遵守。

## 1. 先发现，再假设
- 不假设固定文件名/键名：扫描 `/input`，识别 `case.json`、`task.md`、`materials/`。
- `output_contract` 可能位于 `case.json`（键 `output_contract`/`outputContract`）
  或 `task.md` 内联 JSON；两者都找不到时回退默认语义字段。
- 文本按 `utf-8-sig` → `utf-8` → `latin-1` → `errors="replace"` 依次降级读取；
  单文件超过 2MB 跳过并记入 `degradations`。
- PDF 用平台 `pdftotext` 解析真实文件，不以文件名或标签假定正文语种；必要时
  OCR 兜底。正文语种必须来自实际解析文本，而非来源标签。

## 2. 术语对齐（可独立复核）
每条术语记录：源词、语种、变体（同义词、缩写、上下位、音译、本地译名）、
类型（`synonym|abbreviation|hypernym|transliteration|local_name`）、依据
（`basis`：材料/词典/出版方用词/人工）、验证方式（`verification`：回译、命中
标题回查、原文对拍）。未经验证的译文标 `unverified_translation`，不得当作已核对。

## 3. 逐语种查询与受控翻译
- 每个目标语种单独构造查询，不得只用源语种提问。
- 记录翻译服务与版本、原文—译文对照；译文与原文同义才可作为查询依据。
- 查询须记录 `rationale`（为什么用这个词形，而非字面翻译）。

## 4. 语言与版本判定（每命中必标）
- `language`：条目主体语言；`body_language` 与 `abstract_language` 分开记录，
  避免“英文摘要但葡语正文”被标成英文。
- `version` 取值：`original`、`full_translation`（出版社完整译文）、
  `abstract_only`（仅摘要为另一语种）、`bilingual`、`undetermined`。
- 判定写依据：正文语言、摘要语言、双语刊声明、出版方翻译声明、版本元数据。

## 5. 去重与署名归属
- 同一论文多语版本共用去重键（优先 DOI，无 DOI 用规范化标题+作者+年份）。
- 精确重复条目必须合并并记录 `dedup` 判定依据；版本不同则保留为不同视图。
- 保留 `source`、`DOI`、`version`、`body_language`、`attribution`（作者/期刊/年份）。

## 6. 证据溯源与冲突
- 每条关键结论/数值/命中给 `locator`（URL/DOI/材料段落）与逐字 `quote`。
- 缺失逐字证据标 `pending_human`；同一指标来源不一致时两个都记录并标
  `source_conflict`。
- 不编造引用、DOI、数值；缺失写 `not_reported` 或 `pending_human`。

## 7. 输出契约对齐（本版重点）
- `result.json` 顶层字段与 `output_contract` 逐一对齐：每个契约字段都回填，
  缺值给 `null`/`[]`/`{}`，不改名、不删未知键。
- 无论契约是否存在，始终附加标准元字段：`case_id`、`status`、`sources`、
  `uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、
  `runtime`、`contract_schema`（用于第三方核验对齐关系）。
- 所有变更经 `change_log` 记录：`seq`（递增）、`action`、`fields`（本次触及字段）、
  `status`、可选 `note`。初始化即写入第 0 条；此后只做增量 patch。
- 输出确定性：不使用墙钟时间、不引入随机排序，同一输入重复运行结果一致。
