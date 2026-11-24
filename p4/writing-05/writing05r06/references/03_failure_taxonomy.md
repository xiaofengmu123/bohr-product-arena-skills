# 失败类型与核验方法（可分享口径）

本文件沉淀"文献证据驱动写作"中高频失败类型与对应核验路径，供其他作者/Agent 复用与对齐口径。所有判定均以逐字引文为准，不以记忆或领域标签为准。

## 1. 常见失败类型
### F1 来源表面化（找得到失败）
- 表现：正文只有"(Smith 2020)"，没有指到具体段落/句子；或指到标题/摘要，不指到支撑正文。
- 核验：claim 是否有 `source_doc + locator + quote`；locator 是否可回查；quote 是否在原文逐字存在（脚本 `verify`）。
- 处置：补充段落级 locator 与逐字 quote；否则降级为 `no_support`。

### F2 支撑错位（对得上失败）
- 表现：原文支持"相近但更弱/更强"的话，或引用的是二手转引而非原始研究。
- 核验：`support_verdict` 与 `grade_reason` 是否说明"原文支持范围"；`secondary_citation` 是否标记。
- 处置：重写为与原文一致的强度；二手转引须标记并回查原始出处，无法回查则记 `metadata_unverified`。

### F3 证据强度错配（说得准失败）
- 表现：单篇小样本写成共识；相关写成因果；弱证据写成"证实/确立"。
- 核验：`evidence_grade` 与 `final_wording` 是否相称（脚本对强动词/弱限定词做确定性匹配，`hedge_too_strong` 为阻断项）。
- 处置：按分级口径改写措辞（C/D 用"初步表明/可能"），补 `uncertainty_flags`。

### F4 冲突被隐藏（说得准失败）
- 表现：只呈现支持己方结论的文献，忽略相反证据。
- 核验：材料内是否存在相反结果；`uncertainty_flags` 是否含 `conflict_hidden`。
- 处置：并列呈现两方证据，各给 locator/quote，写入 `uncertainty` 与 `pending_human`。

### F5 材料缺口未标注（复得现失败）
- 表现：缺章节、仅摘要、图表公式不可读、PDF 截断时仍当作完整来源。
- 核验：`limitations`/`degradations` 是否记录缺口。
- 处置：记 `limitations`，不补造内容；无法核验的数值/引用降级 `pending_human`。

### F6 运行失败（可靠体验失败）
- 表现：坏 JSON、坏编码、空目录、缺 pdftotext 导致崩溃或留下空产物。
- 核验：`errors`/`degradations` 是否记录；result.json 是否仍合法可解析。
- 处置：先写 partial 骨架再分析；异常捕获记录，不中断其余材料。

## 2. 核验方法（可复用）
1. 逐字回验：对每条 evidence 的 `quote` 与源文本做规范化子串匹配（NFKC + 去换行连字符 + 空白折叠），产出 `matched/not_found`。
2. 结构校验：claim_id↔evidence_id 双向无孤儿引用；`claim_type`/`support_verdict`/`evidence_grade` 均在枚举内。
3. 表述校验：证据等级与 `final_wording` 强动词/弱限定词匹配检查。
4. 契约校验：result.json 含 `case_id`、题目 `output_contract` 全部字段，及 `status/sources/uncertainty/pending_human/change_log`。
5. 重跑一致：所有脚本确定性执行；`scaffold` 不覆盖非 `in_progress` 结果；`finalize` 由中间产物重新合并可复现。

## 3. 复用边界
- 本套口径适用于"事实—来源—证据强度"可判定的学术写作场景；不替代领域专家的语义判断。
- 所有示例均为接口示意，须标明示例，不得冒充正式案例结果。
