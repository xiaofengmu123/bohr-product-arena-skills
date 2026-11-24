# 自检清单（对应评分维度）

提交/收尾前逐条勾选；自动化部分由 `python3 scripts/selftest.py` 覆盖。

## 结构完整（实际效果 / 可靠体验）

- [ ] `SKILL.md` frontmatter：name/description/version 齐全
- [ ] `result.json`：case_id、status、主产物、sources、uncertainty、pending_human、change_log 齐全
- [ ] `output_contract` 全部字段原样回填，缺值 `null`/`[]`，不改名、不删未知键

## 流程与预算（可靠体验）

- [ ] 写明 900 秒 / 64 请求预算与 16384 token 单响应上限
- [ ] 规定前 3 分钟先落盘 partial 骨架，之后只做增量改进
- [ ] 每步写明输入/输出与请求配额，已有产物跳过重复写盘

## 证据与正确性（实际效果 / 创新增益）

- [ ] 每条关键结论有 `locator` + 逐字 `quote`
- [ ] 号码规范化、同族归并、法律状态与官方登记口径一致
- [ ] 权利要求逐字引用，不越界概括
- [ ] 专利文献与技术文献的检索逻辑分开，同族标 `exhaustive:false`

## 鲁棒性与诚实（可靠体验 / 生态贡献）

- [ ] 空输入 / 坏 JSON / 缺输入退出码 0 且产物合法
- [ ] 缺数据/缺软件如实写 limitations，不编造文献、号码、状态、分数
- [ ] 冲突并列记录并标 `source_conflict`，状态未知写 `unknown`
- [ ] 授权边界逐来源分级（绿/黄/红），受限库不抓取不展示

## 运行自检

- [ ] `python3 scripts/main.py --help` 退出码 0
- [ ] `python3 scripts/main.py validate` 打印 `VALID`
- [ ] `python3 scripts/selftest.py` 退出码 0
