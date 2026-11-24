# 评分维度 → 交付物对照清单（references/scoring-checklist.md）

提交前逐项核对；"证据"指可被独立复核的文件或字段，不是文字自述。

| 评分维度 | 分值 | 本 Skill 的对应交付物 | 检查入口 |
|---|---|---|---|
| 实际效果·任务完成度/结果正确性 | 30 | `result.json.research_product` + `evidence_matrix` 与案例研究问题一致 | 读 `report.md`、`result.json` |
| 实际效果·结果完整性（六类要点） | 5 | 拆解/检索/回链/冲突/结论/空白六点 | `main.py selfcheck` 的 `six_point` |
| 实际效果·结果可核验性 | 5 | `evidence_map.json`、`locator+quote`、`change_log` | 逐条回链材料 |
| 使用增益·质量/效率/可验证 | 25 | 拆解树与检索计划、证据回链率、减少无效检索 | `decomposition.md`、`search_plan.md` |
| 创新增益 | 15 | 问题拆解模板、检索编排、证据综合方法、确定性装配 | `references/determinism-rules.md` |
| 可靠体验·调用易用性 | 3 | `SKILL.md` 调用说明与相对路径脚本 | 按说明重跑一次 |
| 可靠体验·运行稳定性 | 4 | 重复运行字节一致、确定性布局 | `main.py selftest` |
| 可靠体验·异常处理 | 3 | 空输入/坏 JSON/坏编码降级不崩溃 | `main.py selftest` 后两项 |
| 生态贡献·文档/复用/推广 | 10 | `SKILL.md`、`references/`、脚本可复用 | 他人独立安装运行 |

## 提交前硬检查

```bash
python3 -B scripts/main.py selftest    # 退出码 0
python3 -B scripts/main.py --help      # 退出码 0
python3 -B scripts/main.py selfcheck   # valid_json=true 且 problems 为空
```

三者在无正式案例的环境（不同机器/不同 Python 版本）各跑一次，产物可解析、`result.json` 可由 `json.load` 读取。
