# 自检清单（对应评分维度）

提交/交付前逐条勾选。脚本侧由 `scripts/selftest.py` 与
`scripts/main.py selfcheck` 自动验证，模型侧由 Agent 逐条人工确认。

## 可靠性（运行稳定 + 异常处理）

- [ ] 空输入目录 → `status=insufficient_evidence`，退出码 0，不抛异常
- [ ] 坏 JSON / 坏编码 → 记入 `errors`/`degradations`，继续处理其余材料
- [ ] 超大文件（>2MB）→ 跳过并记录，不崩溃
- [ ] 缺 `--input/--output/--work` → 缺省指向 `/input`、`/app/output`、`/app/work`
- [ ] 同一输入重复运行 → 事实映射与校验结果一致（无随机数）

## 完整性（输出契约）

- [ ] `result.json` 可解析，`case_id` 非空，`status` 合法
- [ ] 题目 `output_contract` 全部字段原样回填，未知键未删
- [ ] 含 `status / sources / uncertainty / pending_human / change_log`
- [ ] 前 3 分钟已落盘 partial 骨架，之后只增量改进

## 任务完成度

- [ ] 交付"润色全文 + 逐句修改表"，而非建议或整段重写
- [ ] 覆盖任务要求的所有处理项（逐段、术语表、缩写首次出现、双语对照）

## 正确性 / 语义保持

- [ ] `verify` 报告 `errors == 0`（数字/单位/引用/图表指向/覆盖/溯源）
- [ ] 每条 hedge 警告已"回退或写明理由"，无静默加强/削弱

## 可核验性

- [ ] `fact_source_map` 覆盖所有带数字/引用/关键论断的句子，逐条有
      `locator` + 逐字 `quote`
- [ ] 材料缺口写 `MATERIAL GAP` / `pending_human` / `not_reported`，未编造

## 资源与自检

- [ ] `python3 -B scripts/main.py --help` 退出码 0
- [ ] `python3 -B scripts/selftest.py` 全通过
- [ ] `python3 -B scripts/main.py selfcheck` 返回 OK
- [ ] 产物文件（`manuscript.md`/`glossary.md`）存在且可编辑
