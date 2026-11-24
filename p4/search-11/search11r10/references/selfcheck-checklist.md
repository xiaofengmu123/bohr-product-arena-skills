# 自检清单（对应评分维度）

收尾前逐条勾选；`[ ]` 表示待办，`[x]` 表示完成。此清单与 `scripts/selftest.py` 互补：
清单管语义覆盖，脚本管机械校验。

## 结构完整（可靠体验 / 生态贡献）

- [ ] `SKILL.md` frontmatter 齐备：`name`、`description`、`version`，description 写清触发场景。
- [ ] 输出 `/app/output/result.json` 含：`case_id`、`status`、主产物、`sources`、`uncertainty`、`pending_human`、`change_log`、`runtime`。
- [ ] 若 `case.json` 提供 `output_contract`，其全部字段已原样回填（缺值给 `null`/`[]`，未改名、未删未知键）。
- [ ] 文件数 5–8 个、总体积 60–140KB、`main.py` < 30KB（超出拆 `core_impl.py`）。

## 预算与节奏（可靠体验）

- [ ] 写明 900 秒 / 64 次模型请求预算。
- [ ] 规定前 3 分钟内先落盘一版 `partial` 骨架，之后只做增量改进。
- [ ] 单响应 ≤16384 tokens（含推理）作为预算护栏已写明。

## 执行正确性（实际效果 / 创新增益）

- [ ] `scaffold` 早落盘，空输入/缺输入退出码 0 且状态 `insufficient_evidence`。
- [ ] 号码规范化：公开号/申请号/优先权号 raw + normalized 分开，`number_type` 标对。
- [ ] 同族归并：按共用申请号/优先权号做传递闭包，结果标 `observed` 且 `exhaustive:false`，记录归并 `basis`。
- [ ] 法律状态：`status` + `effective_date` + `source` + `as_of` + `staleness_risk` + `confidence` 齐备。
- [ ] 技术文献：`rank/title/authors/year/venue/doi/source/relevance_rationale/evidence` 齐备。

## 证据溯源（结果可核验性）

- [ ] 每个关键结论（号码、同族、状态、文献相关性）都有 `locator` + 逐字 `quote`。
- [ ] 冲突信息并列记录并标 `source_conflict`，不静默择一。
- [ ] `sources[]` 每条含 `engine/query/url/retrieved_at/count/note`。

## 诚实与降级（异常处理）

- [ ] 缺数据/缺软件 → `limitations` 如实标注，`pending_human` 列缺口，不编造文献、号码、状态。
- [ ] 法律状态未知 → `unknown`，不推断"有效"；过期冲突写 `staleness_risk`。
- [ ] 限流/鉴权失败/检索无命中 → `sources` 与 `limitations` 说明，标 `partial`。

## 机械自检命令

```bash
python3 -B scripts/main.py --help        # 退出码 0
python3 -B scripts/selftest.py           # 必须打印 ALL SELF-TESTS PASSED
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 -B scripts/main.py finalize --output /app/output
python3 -B scripts/main.py validate --output /app/output   # 必须打印 VALID
```
