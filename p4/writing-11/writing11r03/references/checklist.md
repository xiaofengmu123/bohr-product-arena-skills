# 自查清单、已知失败模式与最小调用示例

## 1. 逐项自查清单（发布/收尾前全部勾选）

### 产物完整性
- [ ] `/app/output/result.json` 存在、合法 UTF-8 JSON、可被 `json.load` 读取。
- [ ] 包含 `case_id`，与 `/input/case.json` 一致；`status` 为 `success|partial|failed`。
- [ ] 包含全部 output_contract 字段（`deliverables`/`fact_source_map`/`version_boundary`，或 `case.json` 声明的字段）。
- [ ] 含 `sources`/`uncertainty`/`pending_human`/`limitations`/`change_log`/`runtime`。
- [ ] `deliverables[].path` 指向的文件真实存在；Office/图类工件有渲染 PDF/PNG 预览并写入 `deliverables`。

### 事实与来源映射
- [ ] 每条数字/术语/符号/引用/论断在 `fact_source_map` 有 `locator`+`quote`（claim 必含，其余尽量含）。
- [ ] 未新增材料外事实；推断与原文区分（`note` 标"推断/待核"）。
- [ ] 存疑引用、找不到原文、语义冲突已登记 `uncertainty` 或 `pending_human`。

### 版本与边界
- [ ] `modifications` 每项含 `what`/`why`/`before_after`/`locator`。
- [ ] 未确认项进 `human_confirm`；材料缺口进 `material_gaps`；异常进 `anomalies`。
- [ ] `editable` 为 true 时，工件是可编辑文本/Office，而非只读截图。

### 一致性与确定性
- [ ] 已运行 `scripts/selftest.py` 并保存 `/app/work/selftest_report.json`，退出码 0（或仅 warning）。
- [ ] 术语漂移、数字缺失（启发式）、悬空引用已处理或登记。
- [ ] 同输入两次运行 `deterministic_signature` 一致（时间字段除外）。

## 2. 已知失败模式与处置

| 现象 | 原因 | 处置 |
|-|-|-|
| 术语前后译法不一、图题与正文不一致 | 未建术语表或未锁定 canonical | 先建 `glossary.json`，全篇只写 canonical，跑 selftest |
| 数字被四舍五入、单位错配、million/billion 误读 | 未做数字保真核对 | 原样转写，换算式记入 `fact_source_map` |
| 句段对不上、图表编号错位、脚注丢失 | 未按句段级对照输出 | 用对照表逐段写，公式/题注/脚注原位保留 |
| "suggest" 被译成"证实"、否定范围扩大 | 未守住语义边界 | 按 `bilingual_rules.md` 第 4 节逐级映射 |
| 引用照抄却找不到原文、作者被增删 | 未核对文献条目 | 登记存疑，不改写归属 |
| 结果无法复核 | 未写 `fact_source_map` | 每条论断补 `locator`+`quote` |
| PDF 只凭文件名臆测 | 未真实解析正文 | 用平台 PDF 工具读正文，不读标签 |
| 中途崩溃全丢 | 未先落盘骨架 | 前 3 分钟 `write_result.py init` |
| 同输入两次结果不同 | 随机抽样/随机措辞 | 按 `bilingual_rules.md` 第 5 节执行，跑 selftest 核对签名 |

## 3. 最小调用示例（示意输出，标注为示例）

```bash
# 1) 盘点输入
python3 scripts/read_case.py --brief /app/work/case_brief.json
# -> {"case_id":"case-xxx","output_contract_keys":["deliverables","fact_source_map","version_boundary"],"material_count":2,"encoding_issues":0}

# 2) 落盘骨架（前 3 分钟内）
python3 scripts/write_result.py init

# 3) 增量写产物（示意，非正式案例结果）
python3 scripts/write_result.py add-list deliverables '{"path":"/app/output/zh.md","format":"markdown","title":"译文","description":"整篇中译"}'
python3 scripts/write_result.py add-list fact_source_map '{"item":"样本量","kind":"number","value":"n=128","source":"paper.pdf:3","note":"原文第3页","locator":"paper.pdf:3","quote":"n = 128"}'

# 4) 自检与收尾
python3 scripts/selftest.py --out /app/output/result.json --output-dir /app/output --glossary /app/work/glossary.json --report /app/work/selftest_report.json
python3 scripts/write_result.py finalize --status success
```

以上 JSON 输出为示意，须标明"示例"，不得冒充正式案例结果。

## 4. 复用说明

- 三个脚本仅依赖 Python 标准库，路径参数均可用 `--input/--out/--output-dir/--report` 覆盖默认值。
- `read_case.py` 可在任意输入目录复现 case_brief；`write_result.py` 可用 `--contract` 指定任意 output_contract JSON；`selftest.py` 可用 `--glossary/--source` 接入任意术语表与源材料。
- 空输入目录：`read_case.py` 产出 `case_id=null`、空材料、`input_signature`；`write_result.py init` 仍落盘合法骨架；`selftest.py` 报 case_id 缺失（fail），不崩溃。
