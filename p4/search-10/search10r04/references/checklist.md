# 运行自检清单（references/checklist.md）

提交/交付前逐条确认；对应 `SKILL.md` 第 5 节与题目评分维度。每条都能用 `scripts/selftest.py` 或 `scripts/main.py validate` 验证。

## A. 接口与触发（调用与易用性）
- [ ] SKILL.md frontmatter 含 name / description / version，description 说清何时使用；
- [ ] 输入默认 `/input`，输出默认 `/app/output`，工作目录 `/app/work`；
- [ ] `python3 -B scripts/main.py --help` 退出码 0；
- [ ] 用户能说清源语言、目标语种与已知条目线索并走完一次检索。

## B. 运行与产物（稳定性 / 完整性）
- [ ] 空输入目录：`init` 仍写合法 result.json（`input_error`），退出码 0；
- [ ] 坏 JSON / 坏编码：记入 `errors` / `degradations`，继续处理其余材料；
- [ ] 前 3 分钟内落盘 partial 骨架；中途异常不留下半截 JSON（原子写）；
- [ ] 同一输入重复运行结果一致（无随机性、无时间依赖的排序差异）；
- [ ] `validate` 无 issues：case_id 存在、契约字段齐、status 合法。

## C. 检索质量（实际效果 / 增益）
- [ ] `research_question` 明确源/目标语种、检索范围与已知条目线索；
- [ ] `terminology_table` 逐条带 type / basis / verification，且双向对齐；
- [ ] `queries` 逐语种记录，含翻译服务与版本、原文-译文对照；
- [ ] 每条 `result` 带 locator + 逐字 quote、language 与 version 标注；
- [ ] `dedup` 记录同作品多语版本与精确重复的归并依据；
- [ ] `missing` / `conflicts` / 权限与接口异常如实说明。

## D. 诚实与复核（可核验 / 异常处理）
- [ ] 无编造引用、DOI、数值；缺失标 `pending_human` / `not_reported`；
- [ ] `uncertainty` / `limitations` 显式化，且与证据强度一致；
- [ ] 术语表、查询式、语种判断依据可被第三方独立复核；
- [ ] 翻译失败、语种不确定、原文不可得、版本冲突均有处置记录。

## E. 交付（文档 / 复用）
- [ ] SKILL.md 正文 ≤180 行；文件总数 ≤14；总体积 ≤1.2MB；
- [ ] 脚本仅标准库；调用路径用相对路径（scripts/xxx.py）；
- [ ] 说明与提交版本一致；无预写正式答案或白板。
