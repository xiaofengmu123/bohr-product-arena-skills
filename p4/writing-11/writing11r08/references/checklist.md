# 自查清单、失败模式与评分维度对齐表

本文件是 `SKILL.md` 第 5 节清单的落盘版，供发布前逐条勾选与人工复核。脚本侧自动复核由 `scripts/selftest.py` 与 `scripts/check_consistency.py` 完成，本表同时标明“文字自述不能替代运行证据”——每一行都应有一条脚本输出或落盘文件作为证据。

## 一、评分维度 → 流程/脚本 → 自检证据（本版设计取舍）

本 Skill 不做“通用长文档写作”，只聚焦学术翻译与双语写作的四道质量关，并把题目 100 分评分维度逐条落到可执行的流程与可复核的产物上。

| 评分维度 | 分值 | 评分点 | 对应流程步骤 | 支撑脚本/产物 | 自检证据 |
|-|-|-|-|-|-|
| 实际效果 | 40 | 任务完成度 15 | P2 分块翻译、逐句段产出纯译文/双语对照稿 | `deliverables[]`（可编辑 md/LaTeX/Office） | `main.py validate` 确认路径真实存在 |
| 实际效果 | 40 | 结果正确性 15 | P2 术语/数字/引用抽取 + P3 修订 | `fact_source_map[]`（locator+quote） | `check_consistency.py` 报告无 `number_missing`/`fact_quote_not_found` |
| 实际效果 | 40 | 结果完整性 5 | P2 覆盖章节/图表/公式/脚注/参考文献 | `check_consistency.py` 段落数与图表编号 | 报告 `stats` 无 `alignment_short`/编号跳号 |
| 实际效果 | 40 | 结果可核验性 5 | P3 逐字 quote 溯源 | `fact_source_map[].quote` 可定位 | `check_consistency.py` 的 quote 校验通过 |
| 使用增益 | 25 | 质量增益 15 | P3 术语表锁定 + 数字归一化 | `glossary.json` + `check_consistency.py` | 报告 warnings 逐条关闭 |
| 使用增益 | 25 | 效率增益 5 | 对照稿句段对齐，定位到“文件:行号” | `version_boundary.modifications[]` | 按报告定位即可改，无需通读全文 |
| 使用增益 | 25 | 增益可验证性 5 | 同输入重复运行结果一致 | 脚本确定性、无随机态 | `selftest.py` 复跑 PASS |
| 创新增益 | 15 | 新增能力 5 | 万/亿↔million/billion 归一化比对 | `check_consistency.py:extract_numbers` | 演示用例跨数量级命中 |
| 创新增益 | 15 | 方法改进 5 | 骨架先行+增量改进+二次校验 | `main.py init/add-list/finalize` | 前 3 分钟落盘骨架 |
| 创新增益 | 15 | 创新证据 5 | 术语抽取/分块/对齐约束/风格控制 | `check_consistency.py` 全链路 | `selftest.py` 植入缺陷可被检出 |
| 可靠体验 | 10 | 调用与易用性 3 | 相对路径脚本 + 统一契约 | `SKILL.md` 第 2/4 节 | `main.py --help` 退出码 0 |
| 可靠体验 | 10 | 运行稳定性 4 | 坏 JSON/坏编码/空目录降级 | `main.py`/`check_consistency.py` 容错 | 空输入返回合法 partial |
| 可靠体验 | 10 | 异常处理 3 | 缺软件/语向不支持/超长截断登记 | `uncertainty`/`pending_human`/`anomalies` | `SKILL.md` 第 6 节逐条处置 |
| 生态贡献 | 10 | 文档完整性 3 | 契约、依赖、限制、维护说明 | `SKILL.md` + 本文件 | 说明与提交版本一致 |
| 生态贡献 | 10 | 资源可复用性 4 | 三脚本 + 术语表模板 | `scripts/*.py` + `glossary-template.md` | 他人可独立运行 |
| 生态贡献 | 10 | 分享推广价值 3 | 术语资产/双语经验沉淀 | `glossary.json`、`fact_source_map` | 真实案例可分享 |

## 二、逐项自检清单（发布前必须全部打勾）

### 产物完整性
- [ ] `/app/output/result.json` 存在、合法 JSON、UTF-8、可被 `json.load` 读取。
- [ ] 含 `case_id` 且与 `/input/case.json` 一致；含 `status/sources/uncertainty/pending_human/change_log`。
- [ ] 含 `deliverables/fact_source_map/version_boundary`（或 `case.json` 声明的 `output_contract` 全部字段）。
- [ ] `deliverables[].path` 指向的文件真实存在；Office/图类工件有渲染 PDF/PNG 预览并写入 `deliverables`。

### 事实与来源映射
- [ ] 每条数字、术语、引用、图表编号、关键论断都有 `source` + `locator` + 逐字 `quote`。
- [ ] `quote` 能在原始材料中逐字定位（`check_consistency.py` 校验通过），未新增材料外事实。
- [ ] 存疑引用、找不到原文、结论与文献不符均已登记 `uncertainty`。

### 版本与边界
- [ ] `modifications` 每项含 `what`（改了什么）/`why`（为什么）/`before_after`（差异）。
- [ ] 未确认项全部进 `human_confirm`；材料缺口进 `material_gaps`；异常进 `anomalies`。
- [ ] `editable` 为 true 时，工件是可编辑文本/Office，而非只读截图。

### 一致性（对应四道质量关）
- [ ] 术语：`check_consistency.py` 无 `term_variant_collision`（同一术语多译已统一或登记）。
- [ ] 数字：无 `number_missing`/`unit_missing`/`percent_missing`（数字、单位、量级、年份原样落地）。
- [ ] 对齐：无 `alignment_short`、无图/表编号跳号、无悬空交叉引用。
- [ ] 边界：无 `citation_missing`/`fact_quote_not_found`；否定范围、限定条件、模糊限制语保持原强度。

## 三、已知失败模式与处置

| 现象 | 原因 | 处置 |
|-|-|-|
| 译文堆成顺文字、无句段对应 | 跳过对齐约束 | 先产句段对照稿，再合流为纯译文 |
| 同一术语多种写法、图编号跳号 | 未跑一致性检查 | 跑 `check_consistency.py`，按报告统一 |
| 数字被四舍五入/单位错配 | 未做数字归一化比对 | 用 `extract_numbers` 定位缺失数字 |
| 万/亿与 million/billion 对不上 | 未归一化 | 归一化到同一数量级后再比对 |
| 引用找不到原文却照抄 | 未核对文献 | 登记存疑引用，不改写结论 |
| quote 与原文不符（幻觉） | 未做逐字校验 | `check_consistency.py` 的 quote 校验兜底 |
| 结果无法复核 | 未写 `fact_source_map` | 每条论断补 `locator`+`quote` |
| PDF 只凭文件名臆测 | 未真实解析 | 用平台 PDF 工具读正文，不读标签 |
| 中途崩溃全丢 | 未先落盘骨架 | 前 3 分钟 `main.py init` |

## 四、一键自检命令序列（发布前/收尾时执行）

以下命令全部退出码为 0 才算通过；`check_consistency.py` 与 `selftest.py` 不抛异常，问题记录在报告里而非崩溃。

```bash
# 1. 包结构自检（frontmatter / --help / references / 植入缺陷检出）
python3 -B scripts/selftest.py

# 2. 一致性校验（四道质量关，报告写到 /app/work/consistency_report.json）
python3 -B scripts/check_consistency.py \
  --source /app/work/source_text.txt --target /app/output \
  --glossary /app/output/glossary.json --result /app/output/result.json \
  --out /app/work/consistency_report.json

# 3. 结果契约校验（deliverables 路径真实存在、字段齐全）
python3 -B scripts/main.py --out /app/output validate
```

判定口径：`selftest.py` 必须 `SELFTEST OK`；`check_consistency.py` 报告的 `errors` 必须为空（`warnings` 逐条关闭或登记到 `uncertainty`/`pending_human`）；`main.py validate` 退出码 0。

