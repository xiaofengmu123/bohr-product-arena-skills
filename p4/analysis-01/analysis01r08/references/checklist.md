# 执行清单与降级矩阵

本文件是 SKILL.md 第 7、8 节的展开：逐条可勾选的自检清单、异常场景的降级处置，以及最小调用示例（标注为示例，不冒充正式案例结果）。

## 1. 逐条自检清单（对应 SC1–SC12）

交付前逐条勾选，任何未勾项都必须在 result.json 的 `uncertainty` 或 `pending_human` 中留下记录。

- [ ] **SC1** 已读 `/input/case.json` 与 `/input/task.md`；output_contract 全部键已回填，缺值给 `null`/`[]`，不改名、不删未知键。
- [ ] **SC2** 数据字典、单位/QC 规则、清洗工单均已逐份打开并按内容执行，未按文件名臆测内容。
- [ ] **SC3** 缺失/未检测/低于检出限/真值 0 四类已分开，无插补、无静默改值；每类在 data_profile.json 有计数。
- [ ] **SC4** 每个异常值的保留/剔除/盖帽都有物理上下界与 locator 依据，理由写入 rules.json 与 change_log。
- [ ] **SC5** 重复识别基于真实主键（样本编号/时间戳/受试者标识），近似重复单独报告，保留哪一条及依据明确。
- [ ] **SC6** 编码/分隔符/单位/格式转换可逆或留有映射表与换算因子；需摩尔质量的单位不猜换算，标 pending_human。
- [ ] **SC7** quality_report.md 的数字由 cleaned_data.csv 实时重算，且与 data_profile.json 一致（selfcheck 已验）。
- [ ] **SC8** 每个关键结论/数值有 locator + 逐字 quote；冲突信息两项并列并标 source_conflict。
- [ ] **SC9** 空表/坏 JSON/乱码/单位不明/主键冲突/文件损坏/权限不足均被识别并写入 errors/degradations，进程退出码 0。
- [ ] **SC10** 同一输入两次运行，cleaned_data.csv 与 data_profile.json 逐字节一致（时间戳仅存在于 result.json 的 runtime）。
- [ ] **SC11** 每个处理动作在 rules.json 有 rule_id，在 cell_audit.csv 有对应行（raw_literal/category/action/rule_id）。
- [ ] **SC12** `python scripts/main.py --phase selfcheck` 通过（或 selftest.py 输出 pass）。

## 2. 异常场景降级矩阵

| 场景 | 识别方式 | status | 处置 | 记录位置 |
|---|---|---|---|---|
| 空输入目录 | discover_inputs 无文件 | insufficient_evidence | 写合法 result.json，列出缺口 | pending_human |
| 缺 case.json | load_case 找不到 | input_error | 写骨架，不编造 case_id | errors |
| case.json 坏 JSON | parse_json_robust 非 ok | input_error/partial | 保留原始文本摘要，继续读其它材料 | errors/degradations |
| 乱码/坏编码 | read_text_robust 降级 | partial | GBK/latin-1 容错解码，标注 degraded | degradations |
| 主表为空/无表头 | read_tabular error | partial | 保留原始快照，不产出清洗表 | degradations |
| 单位不明（摩尔浓度等） | Agent 判定 | partial | 不猜换算，标 pending_human | pending_human |
| 规则冲突（同一指标两来源不一致） | Agent 判定 | partial | 两项并列标 source_conflict | uncertainty |
| 主键冲突/不可判定 | find_duplicates 或 Agent | partial | 报告冲突组，保留全部并说明 | uncertainty |
| 文件损坏/权限不足 | 读文件异常 | partial | 记录失败步骤与原因，保留已完成产物 | errors/degradations |
| 写盘失败 | OSError 捕获 | partial | 降级写最小合法 result.json，退出码 0 | errors |

## 3. 最小调用示例（示例，非正式案例结果）

输入目录示意（文件名仅示例，实际文件名不固定）：

```text
/input/
├── case.json            # case_id + output_contract
├── task.md
├── measurements.csv     # 主表（示例）
├── data_dictionary.csv  # 数据字典（示例）
└── qc_rules.md          # 单位/QC 规则（示例）
```

调用：

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
# Agent 读材料后回填 work/rules.json 与 work/evidence_map.json
python scripts/main.py --phase full --input /input --output /app/output --work /app/work
python scripts/main.py --phase selfcheck --output /app/output
```

示意输出（示例，标注为示例）：

```json
{
  "phase": "selfcheck",
  "pass": true,
  "checks": [
    {"item": "result.json parse", "pass": true, "detail": "parse_status=ok"}
  ]
}
```

以上输入与输出均为接口示意，不代表任何正式案例答案。
