# 自检清单（references/checklist.md）

交付/收尾前逐条勾选。括号内为该条对应的评分维度。机械自检由
`python scripts/selftest.py` 与 `python scripts/main.py --phase selfcheck` 完成；
下面标注 ⚙ 的为脚本自动校验，其余为 Agent 人工核对。

## 结果完整性 / 可复核性

- [ ] ⚙ case.json 与 task.md 已读，`result.json.case_id` 非空。
- [ ] ⚙ `output_contract` 全部键已原样回填（缺值 `null`/`[]`，不改名、不删未知键）。
- [ ] ⚙ `status` ∈ `ok | partial | insufficient_evidence | input_error`，且诚实。
- [ ] 主产物（cleaned_data.csv、quality_report.md、data_profile.json、evidence_map.json）路径写入 `artifacts`。

## 实际效果 / 正确性

- [ ] 数据字典、单位/QC 规则、工单均已打开并按内容执行，未按文件名臆测。
- [ ] 未检测 / 低于检出限 / 录入缺失 / 真值 0 四类分开，无插补、无静默改值。
- [ ] 异常值判定有物理上下界与 locator 依据，保留/剔除/盖帽理由成文。
- [ ] 重复识别基于真实主键，保留策略明确。
- [ ] 单位/编码转换可逆或留映射表，精度未丢失。
- [ ] ⚙ 报告数字与 cleaned_data.csv 逐字段一致（CSV 行数 = profile `n_rows`）。

## 证据溯源

- [ ] 每项关键结论有 locator + 逐字 quote。
- [ ] 冲突信息两项并列并标 `source_conflict`。

## 可靠体验 / 异常处理

- [ ] ⚙ 空表 / 坏 JSON / 乱码 / 单位不明 / 主键冲突 / 文件损坏 / 权限不足均被识别并诚实记录，退出码 0。
- [ ] ⚙ 两次运行数据结果一致（确定性）；partial 结果标注局限。
- [ ] 缺数据/缺软件时如实标注 `limitations`/`degradations`/`pending_human`，不编造。

## 交付

- [ ] ⚙ `python scripts/selftest.py` 全部 PASS。
- [ ] ⚙ `python scripts/main.py --phase selfcheck` 全部 `[ok]`。
- [ ] `result.json` 为最终定稿（status 已按 Agent 裁决更新，非骨架 partial）。
