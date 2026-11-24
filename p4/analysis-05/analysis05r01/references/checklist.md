# 自查清单与评分维度对照

提交前逐条勾选。脚本侧对应自检见 `scripts/selftest.py`；本文是人工/Agent 复核口径。

## 1. 交付结构

- [ ] `SKILL.md` 根目录，frontmatter 含 `name/description/version` 三项且为合法 YAML。
- [ ] `scripts/main.py`、`scripts/core_impl.py`、`scripts/selftest.py` 存在，仅用 Python 标准库，用相对路径相互引用。
- [ ] 总文件数 ≤14、总体积 ≤1.2MB、`main.py` < 30KB。

## 2. 结果契约（对应“结果完整性 / 可核验性”）

- [ ] `/app/output/result.json` 含 `case_id` 与题目 `output_contract` 全部字段（缺省时用 `analysis_results/quality_evidence/anomaly_notes` 三键）。
- [ ] 含 `status`、`sources`、`uncertainty`、`change_log`、`pending_human`、`errors`、`degradations`、`runtime`。
- [ ] `evidence_map.json` 每个条目含非空 `locator` 与逐字 `quote`。

## 3. 执行纪律（对应“调用与易用性 / 运行稳定性”）

- [ ] 前 3 分钟落盘 `status=partial` 骨架；后续只增量更新。
- [ ] 同一输入两次运行，`stats.json` 与 SVG 字节一致（无随机、固定排序）。
- [ ] 空目录/坏 JSON/缺列/除零/编码错误 → 退出码 0，异常写进 `errors/degradations`。

## 4. 分析正确性（对应“结果正确性”）

- [ ] 检验方法匹配组数：2 组用 Welch t + Mann-Whitney U；≥3 组用 ANOVA + Kruskal-Wallis；连续两列用 Pearson。
- [ ] 描述统计口径统一：样本方差除以 n−1，中位数/四分位用插值法，秩平均处理并列。
- [ ] 报告 p 值、效应量与 `stats.json` 完全一致；`clean_meta.json` 删行数 == 原始行数 − `cleaned.csv` 行数。

## 5. 诚实标注（对应“异常处理”）

- [ ] 异常只标记不删；缺失只在统计时 listwise，不静默删整行。
- [ ] 无数据 → `insufficient_evidence`；输入坏 → `input_error`；部分失败 → `partial`。
- [ ] 不编造数值、DOI、缺失软件状态；缺材料写 `pending_human`/`not_reported`。

## 6. 生态复用（对应“资源可复用性”）

- [ ] 他人可仅凭 `SKILL.md` 说明跑完一条完整分析链，无需作者个人文件。
- [ ] `scripts/main.py --help` 退出码 0，缺省指向 `/input`、`/app/output`、`/app/work`。
- [ ] 最小调用示例在 `SKILL.md` 或 `README` 中给出，示意输出标明“示例”。

## 7. 快捷自检命令

```bash
python3 -B scripts/main.py --help
python3 -B scripts/selftest.py --input /input --output /app/output --work /app/work
```
