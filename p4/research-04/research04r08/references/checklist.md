# 自检清单与失败定位（checklist）

提交/交付前逐条勾选；每一条对应评分维度标签（SKILL.md 第 8 节 S1–S16）。selftest.py 自动校验其中客观项。

## 1. 自检清单（逐条勾选）

- [ ] S1[A1] result.json 合法 JSON，含 case_id、output_contract 全部字段与固定字段；四类产物均非空。
- [ ] S2[A2] 年份口径写于 protocol.date_definition_note 与 time_trend.unit_note；distribution 有年度计数；每个 claim 有 evidence_ids。
- [ ] S3[A3] 时间跨度、主题层级、各阶段代表作、空白类型（open_question/method_failure/unresolved_conflict/coverage_absence）覆盖齐全。
- [ ] S4[A4] evidence_map 每条 quote 为原文逐字且可定位；selftest 无“未定位”硬错误；tables/*.csv 存在。
- [ ] S5[A2] 代表作 criteria 含 kind 口径，不只按被引排序；空白项均写 exclusion_evidence。
- [ ] S6[U1] machine 基线在深度分析前已生成；候选→结论的升级记录于 change_log。
- [ ] S7[U2] 不重复检索/不手工补年份/不重复统计：由 scripts/main.py 完成机械抽取。
- [ ] S8[U3] runtime 记录 started_at/finished_at/elapsed_s/model_requests；change_log 逐次追加。
- [ ] S9[I1] 时间窗划分、术语首末年份、kind 选取有方法说明（machine.term_stats + windows.basis + criteria）。
- [ ] S10[I2] 年份归一、术语归并、空白判据的规则写入 protocol / term_mapping / exclusion_evidence。
- [ ] S11[I3] excluded_not_gaps 与 coverage_absence 证明结论不是“检索前 N 条”或“没检索到”。
- [ ] S12[R1] SKILL.md frontmatter（name/description/version）齐备；脚本路径为相对路径，可独立运行。
- [ ] S13[R2] 重复运行结果一致（确定性，无随机数）；先落盘骨架再增量，中断不留空白产物。
- [ ] S14[R3] 空输入/坏 JSON/年份缺失/文献爆炸/证据冲突/PDF 不可读均如实标 limitations/errors/degradations。
- [ ] S15[C1] 使用说明、依赖（仅标准库）、时间窗约定、边界与维护信息齐全（SKILL.md + references/）。
- [ ] S16[C2] 脚本、词表、统计表可复用；无编造 benchmark 分数、无预置答案、无伪造文本层。

## 2. 自检运行方式

```bash
python3 scripts/main.py                       # 骨架 + 机械基线（首次）
python3 scripts/selftest.py                   # 校验契约/枚举/quote/alignment
python3 scripts/selftest.py --strict          # WARN 也判失败（提交前）
```

- selftest 结果写入 `/app/work/selftest.json`（逐项 PASS/WARN/FAIL），并回填 `result.json.alignment`。
- 退出码：0 = 通过；1 = 存在 FAIL；2 = result.json 缺失。

## 3. 四类深度分析的质量门槛

1. time_trend：先确认每个 doc 的年份来源（metadata 优先，其次题录/DOI/PDF 页眉），再统计；禁止无证据猜年份；区分“离散波动”与“真实拐点”。
2. topic_evolution：term_mapping 人工归并（同义词/变体），再判 emergence/merge/split/decline/terminology_drift；每类事件写 cause 与证据；同名异义写 uncertainty。
3. representative_works：按阶段分桶，每阶段按 kind（origin/method/milestone/benchmark）覆盖，避免只取高被引，覆盖长尾方法首创。
4. research_gaps：每个空白写检索式 + 排除条件 + 反例排查；无法排除写 coverage_absence，可排除写 open_question。

## 4. 失败定位与恢复

- 任一脚本失败：读 `/app/work/selftest.json` 定位 FAIL 项；结构错误手工修复 result.json，再复跑 selftest。
- 若 main.py 已落盘骨架，即使后续失败，result.json 仍是合法 partial 结果；把失败原因写入 errors/limitations。
- 恢复顺序：`python3 scripts/main.py --force` → 手工修复 → `python3 scripts/selftest.py`。
- 无法定位的 quote：改为精确逐字引用，或置 `status=not_reported` 并写入 uncertainty/pending_human。

## 5. 判定 status 的规则

- `ok`：四类产物齐全，关键 claim 均有证据回链，无未核验的 machine 候选，selftest 无 FAIL。
- `partial`：产物可打开但不完整，或部分结论待人工复核（骨架/机械基线阶段即为此态）。
- `insufficient_evidence`：材料不足以支撑趋势/空白判断，如实标注，不编造。
- `input_error`：输入契约不可解析且无法恢复。

## 6. 禁止事项

- 禁止编造 benchmark 分数、白板或官方对照结果；平台自记 runtime/请求数。
- 禁止把检索前 N 条当前沿、关键词热度当趋势、总被引当代表作、没检索到当空白。
- 禁止伪造 PDF 文本层、引用、DOI、数值；缺失一律 not_reported 或 pending_human。
