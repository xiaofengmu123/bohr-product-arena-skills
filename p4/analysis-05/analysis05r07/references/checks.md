# 提交前自检清单（对应评分维度）

交付前在干净目录跑一遍 `python3 -B scripts/selftest.py`，并逐条确认。任何未勾选项都要在 `SKILL.md` 或 `result.json` 中说明。

## A. 契约与骨架

- [ ] `SKILL.md` 顶部 frontmatter 含 `name / description / version`，正文 ≤180 行。
- [ ] 脚本调用使用相对路径 `scripts/xxx.py`，且文件真实存在。
- [ ] 骨架在前 3 分钟内落盘 `/app/output/result.json`（status=partial），之后只增量更新。
- [ ] `result.json` 含 `case_id` + 题目 `output_contract` 全部字段（原样回填、不改名、缺值给 null/[]）+ `status / sources / uncertainty / pending_human / change_log`。

## B. 数据与清洗

- [ ] `work/profile.json` 有字段字典（类型/行数/列数/主表）。
- [ ] `work/clean_meta.json` 的去重行数 == 原始行数 − `work/cleaned.csv` 行数。
- [ ] 异常值只标记（IQR 1.5×），不删除；缺失值按列记录，不静默填补/删除。

## C. 统计与证据

- [ ] 检验方法匹配组数：2 组 Welch t，≥3 组 ANOVA + Kruskal-Wallis；Levene 校验方差齐性。
- [ ] 报告 p 值/效应量与 `work/stats.json` 同源，`p<0.0001` 显示为 `<0.0001`，真实值留在 stats.json。
- [ ] 每个关键结论有 `locator + 逐字 quote`（`sources` 数组）。
- [ ] 同一输入两次运行，`stats.json` 与 SVG 字节一致（无随机数）。

## D. 降级与健壮性

- [ ] 空输入目录 → 退出码 0，写合法 result.json（诚实 status）。
- [ ] 坏 JSON / 坏编码 → 记入 `errors/degradations`，继续处理其余材料。
- [ ] 缺参数 → 缺省 `/input /app/output /app/work`；未知参数忽略不崩溃。
- [ ] 超大文件（>2MB 文本 / >200MB SQLite）→ 跳过或确定性抽样，记入 degradations。
- [ ] 常数列 / 除零 / 样本不足 → 检验返回 `skipped/no_difference`，不伪造 p 值。
- [ ] 缺软件（无 pandas/scipy/matplotlib）→ 纯标准库核心交付不受影响，不编造“已用某库”。

## E. 产物

- [ ] `/app/output/result.json`、`report.md`、可编辑 `figure_*.svg` 存在。
- [ ] `result.json` 可被 `json.load` 解析；SVG 可被 `xml.etree.ElementTree.parse` 解析。
- [ ] `report.md` 正文数字全部来自 `stats.json`，无手写数字。

## F. 边界

- [ ] 不预置正式测试案例、不预写答案、不编造 DOI/分数/结论。
- [ ] 无真实材料时输出“材料不足”，不用示例填充。
