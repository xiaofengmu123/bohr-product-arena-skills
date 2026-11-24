# 引用与来源核验规则（语义阶段由 Agent 执行）

脚本只做确定性部分；以下语义判断由 Agent 按本规则完成并回写
`evidence_map.json` 与 `change_log`，脚本不臆造这些结论。

## 1. 论断句识别与引用定位

1. 逐句扫描 `manuscript.md`，识别"需要来源支撑"的论断句：含具体数值、
   方法来源、结论归属、比较性陈述、名词定义或转述他人工作的句子。
2. 引用挂在**真正需要来源的那句话/那个位置**，而不是整段末尾或图表注附近。
3. 一句多引：多个来源支撑同一论断时写 `[n1,n2]`（编号升序）。
4. 多句一引：同一来源支撑相邻多句时，在首个论断句处引用，后续同段不再重复堆砌，
   除非跨段需要重新锚定。
5. 每个引用点回写 `evidence_map.json` 的一条 `entries`：
   - `locator`：原文定位（如 `manuscript.md 字符 12-24` 或章节段落号）；
   - `quote`：**逐字**引用被支撑的那句/片段（不得改写）；
   - `matched_source_key`：命中的文献库条目 source_key；
   - `basis`：匹配依据（题名/结论主题/作者/年份/DOI）；
   - `status`：`matched | uncertain | unresolved | not_reported`。

## 2. 论断 ↔ 文献匹配

- 优先匹配**主题与结论**：正文论断必须与文献条目摘要/题名表达的主张一致，
  而不是只看关键词重叠。
- 元数据（作者、年份、期刊、DOI）必须来自文献库或材料原文，不得补造。
- 找不到支撑的论断 → `status=not_reported`，写入 `pending_human`，正文保留
  占位并标注"未找到来源"；**严禁**用不相干条目硬凑。

## 3. 去重与归并

- 脚本按 DOI 或 规范化题名+第一作者+年份 自动合并，依据写入 `merge_evidence`。
- 跨语言题名（中/英）、预印本 vs 正式版、不同数据库导出字段差异：脚本只标
  `dedup_candidate`，**由 Agent 确认后才合并**，并在 `change_log` 记录确认依据
  （如 DOI 相同、作者+年份+卷期一致）。
- 同名作者的不同文献、同 DOI 的不同版本**不得**自动合并；歧义写
  `source_conflict` / `pending_human`，不隐藏。

## 4. 格式转换与字段补齐

- 转换目标：`gbt7714`（GB/T 7714—2015 顺序编码制）或 `apa`（作者—出版年制）。
- 保留 DOI、卷、期、页、文献类型标识等关键字段；中英文混排与标点差异按目标规范
  处理；缺失字段写 `[未标注XX]`，不得编造。
- 缺 Office/PDF 渲染软件时交付 Markdown 可编辑版，并在 `limitations` 中说明；
  若任务要求 PDF/PNG 预览，使用运行时 `office-renderer` / `pdf-render-tool`。

## 5. 来源核验

- 通过 DOI、数据库检索或原文比对核验条目真实性；核验结果分级记录：
  `verified`（有原文/DOI 回查）、`not_verified`（未回查）、`source_conflict`
  （同一指标两来源不一致，两者都记录）。
- DOI 解析失败、条目歧义 → 标 `not_verified`/`pending_human`，**不得声称已核验**。
- 每个关键结论/数值必须有来源定位与逐字 quote；缺失写 `not_reported`。

## 6. 冲突与诚实原则

- 材料不足、冲突、需人工确认的内容明确标注，不掩盖、不填充。
- `status` 如实：`ok` 只能在语义核验完成后设置；材料不足为
  `insufficient_evidence`；解析失败但不影响主流程为 `partial`；输入不可用为
  `input_error`。
- 严禁编造评测分数、DOI、元数据、检索结果或实验数据。
