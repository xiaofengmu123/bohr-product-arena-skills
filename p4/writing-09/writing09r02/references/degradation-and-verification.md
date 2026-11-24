# 降级矩阵、校验规则与 status 语义

## 1. status 语义（诚实标注）

| status | 含义 |
|---|---|
| `in_progress` | 骨架已落盘、内容未填满（一般不作为最终态提交） |
| `partial` | 已交付可编辑产物但不完整：部分字段/证据缺失，已标注缺口 |
| `complete` | 产物完整、自检通过、无未标注缺口 |
| `insufficient_evidence` | 材料不足以完成核心写作，如实说明缺口，不编造 |
| `failed` | 运行异常/无法完成，result.json 仍落盘并记录原因 |

status 必须与 change_log、limitations、uncertainty 一致：`partial` 应列缺口，`failed` 应列失败原因。

## 2. 降级矩阵（禁止编造）

| 场景 | 处置 |
|---|---|
| 缺数据/缺图/缺表 | `uncertainty` 记“缺失”，字段写 null 或留空；不补造 |
| 缺统计量（无 p/n/检验） | 正文写“未报告”，不推断显著性；进 uncertainty |
| 单位不明 | 写 `unit_unknown`，不猜单位；进 pending_human |
| 误差棒含义不明 | 写“图注未说明误差棒含义”，标 `chart_reading_unclear` |
| 图表读数不清/分辨率不足 | 保留可读部分，标 `chart_reading_unclear`；不猜刻度 |
| 引用找不到/无 DOI | 保留材料原文位置，标 `unverified`；不伪造 DOI/URL |
| 信息冲突（两来源不一致） | 两条都记录，标 `source_conflict`，进 version_boundaries |
| 输入超长/跨度过大 | 截取关键段落，在 uncertainty 说明“已截断/未覆盖” |
| 软件不可用（pdftotext/office 渲染等） | 写 limitations，交付纯文本/结构化产物，不假装已渲染 |
| 运行失败 | status=failed，change_log 记原因与已完成中间产物 |

## 3. 校验规则（脚本 + 人工）

**脚本强制**（`scripts/check_result.py`）：

1. result.json 可解析；
2. case_id 存在且与 input_summary 一致；
3. output_contract 全部字段存在；
4. status 在合法集合内；
5. change_log 非空；
6. sources / facts_sources 逐条含 locator + 逐字 quote；
7. locator 可回指材料清单中的文件；
8. manuscript 无禁词（材料原句引用除外）；
9. Results 段无因果/解释性动词（越界提示）。

**人工必核**（脚本无法替代）：

- 每个数值与源图/源表逐位比对（脚本只查“有没有 locator”，不查“读数对不对”）；
- 语气强度与证据强度是否匹配；
- 结果段是否混入解释、讨论段是否只堆引用不讲关系；
- 双语/格式是否满足受众与文体要求。

## 4. 自检清单（与 SKILL.md 一致，逐条勾选）

- [ ] 任务完成度：交付可编辑、可投稿的 Results 与 Discussion 章节稿，非建议/提纲/模板。
- [ ] 结果正确性：每个数值/单位/误差棒/统计量与原始图表一致，无材料外新增事实。
- [ ] 结果完整性：图表、阴性结果、不一致文献与局限均被如实交代。
- [ ] 结果可核验性：正文数值与讨论论断可独立复核。
- [ ] 一致性：结果段只述事实、讨论段承担解释；趋势/差异/因果语气与证据强度匹配。
- [ ] 效率：脚本归一化→早落盘→自动回检，减少起草与返工。
- [ ] 来源可溯：facts_sources 覆盖关键论断，区分本研究数据 vs 外部文献。
- [ ] 方法改进：读得准/分得清/说得住/核得了四关由脚本强制回检。
- [ ] 无空话修饰：无“首次/国际领先/填补空白/颠覆性”等无据措辞。
- [ ] 调用稳定：长材料/多图表/重复运行下产出口径一致。
- [ ] 异常处理：缺图/缺统计/单位不明/引用找不到已入 uncertainty/pending_human/limitations。
- [ ] 资源可复用：SKILL.md、规则、检查清单、降级规则齐全。
- [ ] 结构合法：result.json 含 case_id + 全部 output_contract 字段 + status/sources/uncertainty/change_log。
- [ ] 未编造：status/sources/uncertainty/change_log 与真实执行一致。
