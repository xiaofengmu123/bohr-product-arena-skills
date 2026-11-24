# 交付前自检清单（逐条可勾选）

对应本题评分维度：实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10。
交付或提交前逐条勾选；脚本侧已自动化的项标注 `[auto]`。

## A. 结果可核验（实际效果 40）

- [ ] 从原始表推进到报告：导入、字段核对、清洗、分析、可视化、报告五段在同一环境串通。`[auto]` `python3 -B scripts/main.py run` 一步跑通。
- [ ] 数值正确：统计量与 p 值与独立复算一致（对拍 `work/stats.json` 与 scipy 同口径结果）。`[auto]` `scripts/selftest.py` 内含统计对拍。
- [ ] 处理规则与执行一致：`work/clean_meta.json` 中 `consistency_check == true`。`[auto]`
- [ ] 题目要求的字段、检查点与产物覆盖：`result.json` 含 case_id + output_contract 全部字段。`[auto]` `main.py check` 校验。
- [ ] 证据链完整到可重跑：`result.json.sources` 每项有 locator + 逐字 quote；`artifacts` 含各产物 sha256。`[auto]`

## B. 使用增益（25）

- [ ] 质量稳定提升：同一输入重复运行结果一致，不因顺序/时间漂移。`[auto]` 自检确定性用例（两次运行字节对比）。
- [ ] 效率：减少人工改代码/补字段/重跑；一条 `run` 命令完成全链，失败只重跑缺失阶段（幂等）。
- [ ] 增益可验证：`scripts/selftest.py` 以可复跑方式证明正确性与确定性，而非文字自述。

## C. 创新增益（15）

- [ ] 新增能力：SQLite 库发现与查询、D'Agostino 正态性 + Levene 方差齐性校验、Bonferroni 事后比较，纯标准库实现。`[auto]`
- [ ] 方法改进：按前提选择参数/非参数检验并保留 sensitivity（同报另一种方法结果），多重比较校正，异常只标记不删。
- [ ] 创新边界：`references/methods.md` 写清每个检验的适用条件、近似与不适用情况。

## D. 可靠体验（10）

- [ ] 调用易用：`scripts/main.py --help` 退出码 0；相对路径调用，缺省 `/input` `/app/output` `/app/work`。`[auto]`
- [ ] 运行稳定：空输入、坏 JSON、缺列、类型不符、除零、编码错误、SQLite 失败均退出码 0 且说明。`[auto]` 自检覆盖。
- [ ] 异常处理：所有异常就地捕获并写入 errors/degradations，不静默、不崩溃。

## E. 生态贡献（10）

- [ ] 文档完整：SKILL.md + references 两文件写清用途、契约、方法、边界、自检。
- [ ] 资源可复用：脚本仅标准库，路径相对，他人可复制后直接 `python3 -B scripts/selftest.py` 验证。
- [ ] 分享推广：`references/methods.md` 可迁移到同类多步数据分析流程。

## 运行验证命令

```bash
python3 -B scripts/main.py --help                 # 退出码 0
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py check --input /input --output /app/output --work /app/work
python3 -B scripts/selftest.py                     # 应输出 PASS=n FAIL=0
```
