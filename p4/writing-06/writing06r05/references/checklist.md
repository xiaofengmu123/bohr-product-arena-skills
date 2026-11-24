# 交付前自检清单（逐条可勾选，对应评分维度）

交付前按此清单逐条勾选；每条对应 SKILL.md 第 5 节的评分维度，verify.py 只做机器可校验的部分，以下语义项需模型自查。

## 实际效果（40 分）

- [ ] 任务完成度：diagnosis 已产出，为可编辑 Markdown；edit_plan 逐条给出 `action/from/to/instruction/reason/quote`，作者可直接执行。
- [ ] 正确性：每条判断与稿件实际内容相符，无虚构缺陷、无漏报真实断裂、不把风格偏好当结构错误（对照 references/structure-taxonomy.md §4）。
- [ ] 完整性：六维（研究问题/章节结构/论证链/段落关系/重复内容/结论边界）均有覆盖；若某维不是稿件主要问题，已说明"未发现主要缺陷"而非留空。
- [ ] 可核验性：sources/source_map 中每条关键论断都有 locator + 逐字 quote；quote 原样复制，无增删。

## 使用增益（25 分）

- [ ] 质量：已用 `/app/work/evidence.json` 的段落切分与候选论断定位，而非凭记忆。
- [ ] 效率：作者从拿到诊断到定稿的轮次可预期，edit_plan 直接指向修改位置。
- [ ] 可验证：change_log 记录了骨架落盘与每次增量修改的时间与动作，返工可见。

## 创新增益（15 分）

- [ ] 新增能力：论证链以"论断—论据映射表"（references/structure-taxonomy.md §3）给出。
- [ ] 方法改进：结论边界用"相关性→因果动词 + 数据范围"对照表（§2）检查。
- [ ] 创新证据：产物与"语言润色类工具"边界清晰——只改结构、不重写全文。

## 可靠体验（10 分）

- [ ] 易用/稳定：SKILL.md 触发条件明确；`python scripts/main.py`、`verify.py`、`selftest.py` 均以相对路径可跑；重复运行结果一致。
- [ ] 异常处理：缺章节、无参考文献、图表/公式缺失、格式混乱、稿件被截断均已在 uncertainty/version_boundary 标注，未编造。

## 生态贡献（10 分）

- [ ] 文档完整：用途、依赖（仅 Python 标准库）、路径、范围、限制、失败处理齐全。
- [ ] 可复用：六维分类、动作集、locator 规范、映射表模板、脚本均可被他人直接复用。

## 终检（机械项，交给脚本）

- [ ] `python scripts/verify.py` 输出 `"fail": 0`（warn 可保留并说明）。
- [ ] `python scripts/selftest.py` 退出码 0。
- [ ] `/app/output/result.json` 为合法 UTF-8 JSON，含 case_id、status、sources、uncertainty、pending_human、change_log 与题目 output_contract 全部字段。
