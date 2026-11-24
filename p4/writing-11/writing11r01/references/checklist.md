# 自查清单与已知失败模式（checklist.md）

发布/交付前逐条打勾；对应 SKILL.md 第 5 节的评分维度。

## 逐项自查

### 产物完整性
- [ ] `/app/output/result.json` 存在、合法 JSON、UTF-8、可被 `json.load` 读取。
- [ ] 包含 `case_id` 且与 `/input/case.json` 一致。
- [ ] 包含题目 output_contract 全部字段（默认 `deliverables` / `fact_source_map` / `version_boundary`）。
- [ ] 含 `status` / `sources` / `uncertainty` / `pending_human` / `change_log` / `evidence_map`。
- [ ] `deliverables[].path` 指向的文件真实存在；Office/图类工件有渲染 PDF/PNG 预览并写入 `deliverables`。

### 证据溯源（本版强化）
- [ ] `evidence_map.segments/terms/numbers/citations/alignment` 每条都有 `source_locator` + 逐字 `source_quote`。
- [ ] 每条关键结论、数字、术语、引用都能指回原文片段，不新增材料外事实。
- [ ] `fact_source_map` 为 `evidence_map` 的粗粒度镜像，逐条可回溯。
- [ ] 推断与原文明确区分（`note` 标注"推断/待核"）；存疑引用、找不到原文均已登记 `uncertainty`。

### 四"稳"复核
- [ ] 术语稳：同一术语全篇同一译法，缩写/全称一致，无同词异译。
- [ ] 数字实：数字、单位、量级、年份、有效数字与原文一致，万/亿换算已显式标注。
- [ ] 对得齐：句段一一对应，段落顺序、公式、表格、题注、脚注、参考文献原位保留。
- [ ] 边界清：否定范围、因果强度、模糊限制语、引用归属未改变，无凭空结论。

### 版本与边界
- [ ] `modifications` 每项含 `what`/`why`/`before_after`。
- [ ] 未确认项进 `human_confirm`/`pending_human`；缺口进 `material_gaps`；异常进 `anomalies`。
- [ ] `editable` 为 `true` 时工件是可编辑文本/Office，而非只读截图。

### 一致性
- [ ] 已运行 `scripts/main.py check` 并保存报告。
- [ ] error（数字漂移、evidence 缺 locator/quote、deliverable 缺失）已全部处理或交人工确认。
- [ ] warning（术语变体、换算待核、悬空引用、句段错位）已处理或登记。

## 已知失败模式（对号入座处置）

| 现象 | 原因 | 处置 |
|-|-|-|
| 同词异译、缩写多义 | 未建术语表或未锁定译法 | 先抽取术语建 glossary，锁定后全文统一 |
| 数字被四舍五入/换单位 | 未做数字抽取与漂移比对 | 逐段 `extract_numbers` 比对，换算写 note |
| 万/亿与 million/billion 静默换算致量级错 | 未显式换算 | `note` 写明换算关系并交人工核验 |
| 引用编号/作者年份丢失 | 未保留引用标记 | 逐段比对 `[...]` 引用，缺失登记并补回 |
| 否定范围被移动、may 被升级成证明 | 未守语义边界 | 按规则 4 复核，不升级/降级强度 |
| 译文无法逐句追溯 | 未写 evidence_map | 每句补 `source_locator`+`quote` |
| PDF 只凭文件名臆测内容 | 未真实解析 | 用平台 PDF 工具读正文，不读标签 |
| 中途崩溃全丢 | 未先落盘骨架 | 前 3 分钟 `init`，之后只增量改进 |
| 结果不可复核 | 无 fact_source_map/evidence_map | 每条结论补 source 定位 |

## 本地验证（不依赖正式案例）

```bash
python3 -B scripts/selftest.py                 # 退出码 0，全部 PASS
python3 -B scripts/main.py --help              # 退出码 0
python3 -B scripts/main.py discover --input ./demo/input --work ./demo/work
python3 -B scripts/main.py init --case ./demo/input/case.json --out ./demo/output/result.json
python3 -B scripts/main.py add-evidence --result ./demo/output/result.json segments '{"id":"s1","source_locator":{"file":"p.md"},"source_quote":"..."}'
python3 -B scripts/main.py check --result ./demo/output/result.json --out ./demo/work/report.json
python3 -B scripts/main.py finalize --result ./demo/output/result.json --status partial
```

空目录、坏 JSON、缺 locator/quote 三种情况各跑一次，确认脚本返回可解释结果而非崩溃。
