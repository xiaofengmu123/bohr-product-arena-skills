# 自检清单（对应评分维度）

收尾前逐条勾选。脚本自检：`python3 scripts/selftest.py`（10 项全过、退出码 0）。

## 实际效果（40 分）
- [ ] 已明确研究问题（`research_question`）与检索范围（`retrieval_scope`）
- [ ] 号码类：先定位再核验，公开号/申请号/优先权号口径分开（`number_type` 不混用）
- [ ] 主题类：用分类号/申请人/引证组合构造检索式，区分专利文献与技术文献
- [ ] 每条专利记录含权利要求逐字引用（`locator` + `quote`），不越界概括
- [ ] 同族已按共用申请号/优先权号归并并标注口径（`observed`、`exhaustive:false`）
- [ ] 法律状态以官方登记为依据，含 `as_of` 与 `staleness_risk`
- [ ] 授权边界逐项分级（绿/黄/红）并写降级方案

## 结果可核验性（实际效果内的 5 分）
- [ ] 每条结论有 `locator` + 逐字 `quote`
- [ ] `sources[]` 每条含 `engine/query/url/retrieved_at/count/note`
- [ ] 冲突不静默择一，双方证据并列标 `source_conflict`
- [ ] 检索式、来源版本、查询日期、字段抽取规则可被他人复跑

## 使用增益（25 分）
- [ ] 号码规范化、同族自动归并、骨架落盘为可复用脚本（非一次性人工步骤）
- [ ] 前 3 分钟落盘合法 partial，后续只做增量改进（避免重复检索）
- [ ] 重复运行结果一致（确定性输出、固定排序）

## 创新增益（15 分）
- [ ] 号码规范化/同族归并/状态核验为机械可复核规则，不依赖网页摘要拼凑
- [ ] 受限数据下有能力降级方案（不登录、不绕过、标 degradations）
- [ ] 能说明相对基础 Agent 的边界与价值（白板对照，非自述）

## 可靠体验（10 分）
- [ ] `python3 scripts/main.py --help` 退出码 0
- [ ] 空输入/坏 JSON/坏编码/超大文件/缺参数/未知子命令均优雅降级、退出码 0
- [ ] 限流/鉴权失败/无命中如实写 `sources`+`limitations`，不补造文献
- [ ] 写盘失败降级为最小合法 result.json，进程不崩溃

## 生态贡献（10 分）
- [ ] SKILL.md 写明用途、适用条件、调用方式、输入输出、异常处理
- [ ] 脚本纯标准库、相对路径可执行，无个人文件/手工步骤依赖
- [ ] 规范、字段映射、同族归并规则、授权核验清单可被他人复用
- [ ] 最小调用示例标注为「示例」，不冒充正式案例结果

## 收尾必跑
```bash
python3 scripts/main.py --help        # 退出码 0
python3 scripts/main.py scaffold      # 早落盘
python3 scripts/main.py finalize      # 校验 + 补 runtime + 生成 report.md
python3 scripts/main.py validate      # 必须打印 VALID
python3 scripts/selftest.py           # 10 项全过、退出码 0
```
