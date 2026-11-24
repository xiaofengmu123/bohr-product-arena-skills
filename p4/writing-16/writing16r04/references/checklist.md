# 自检清单与证据规则（checklist）

本文件是 SKILL.md 执行流程的配套核对表，供 Agent 在改写过程中和交付前逐条勾选，也供他人复核。每条对应评分维度的关键点。

## 1. 事实与来源（对应“结果正确性 / 完整性”）

- [ ] 每个数值（含单位、比较基准、百分比、样本量、效应量）都能指回源文逐字片段。
- [ ] 图表编号、引用编号、文献条目与源文一致，未新增源文之外的引用或 DOI。
- [ ] 统计口径（如 CI、p 值、OR/RR、N、随访时间）在改写前后含义不变。
- [ ] 未出现源文没有支持的新结论；所有新增解释只解释、不扩展结论。

## 2. 论断强度（对应“说得准”）

改写前对每个关键论断标注强度，并按下表保留表述：

| 强度标签 | 源文证据 | 改写要求 |
|-|-|-|
| `fact` | 已确立/直接陈述的事实或数值 | 保留原强度，逐字核对数字与单位 |
| `causal` | 明确的因果结论 | 保留因果词并带限定条件与依据 |
| `correlational` | 相关性/关联 | 不改写为因果，保留“相关/关联” |
| `preliminary` | 初步/探索性结果 | 保留“初步表明”等限定语，不升格 |
| `not_reported` | 源文未报告 | 不补造；写入 pending_human 或标“未报告” |

## 3. 术语锁定（对应“读得懂 / 术语一致”）

- [ ] 每个缩写首次出现时展开全称；后续保持同一缩写，不混用全称/缩写。
- [ ] 同一概念全文只有一个说法（同义词收敛），并在术语表中记录锁定写法。
- [ ] 跨文体替换术语时，术语表同时保留“源文术语 → 目标读者术语”映射。

术语表最小字段：`term`（锁定写法）、`source_term`（源文写法）、`expansion`（缩写全称）、`audience_note`（目标读者解释/类比）、`locator`（源文定位）。

## 4. 逐段对照（对应“接得住 / 可核验性”）

- [ ] 每处删改都有记录：`locator`（源文段落/行）、`before`、`after`、`reason`、`type`。
- [ ] `type` 只用五类之一：`keep`（保留）、`compress`（压缩）、`delete`（删除冗余）、`reword`（改写措辞）、`add_gloss`（增补解释，非新事实）。
- [ ] 删改理由能回答“删掉的是冗余还是证据”；删除证据需在 `change_log` 中明确标为待确认。
- [ ] 无法判断、材料冲突、涉密/未发表内容进入 `pending_human`，不擅自定论。

## 5. 输出契约（对应“结果完整性 / 异常处理”）

- [ ] `/app/output/result.json` 存在且为合法 JSON。
- [ ] 含 `case_id` 与题目 `output_contract` 的全部字段（缺值给 `null`/`[]`，不改名不删未知键）。
- [ ] 含 `status` / `sources` / `uncertainty` / `change_log`；`status` 只能取 `ok | partial | insufficient_evidence | input_error`。
- [ ] 可编辑主产物（`.md`/`.docx`/`.pptx`/`.xlsx`）已写入 `/app/output/`；Office/幻灯片/图表类附真实渲染的 PDF/PNG 预览。

## 6. 自动化自检（提交前本地跑一次）

```bash
python3 -B scripts/main.py --help          # 退出码 0
python3 -B scripts/selfcheck.py            # 全部 PASS，退出码 0
```

脚本覆盖：空目录、坏 JSON、缺 case_id、带 `output_contract` 的最小案例、finalize 校验。
