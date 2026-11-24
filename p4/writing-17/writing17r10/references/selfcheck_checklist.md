# 自检与评分维度对照清单

本清单把 SKILL.md 第 5 节的自检项与题目评分维度逐条对应，供 Agent 在交付前复核；也供他人复用本 Skill 时判断质量。

## 1. 实际效果（40 分）对照

| 自检项 | 判据 | 对应评分点 |
|-|-|-|
| 产物可打开可编辑 | `mindmap.svg` 浏览器可开、`.mm` 可被 XMind/FreeMind 打开、`.md`/`.json` 非空 | 任务完成度 |
| 节点忠于材料 | 每个节点 label 可在材料找到依据，无装饰词、无外部事实 | 结果正确性 |
| 关系方向正确 | 因果/依赖/支持方向与材料一致，无凭空连线 | 结果正确性 |
| 覆盖关键要素 | 研究问题/方法/证据/结论/任务/风险/空白均有分支 | 结果完整性 |
| 证据可回溯 | 关键节点与关系有 `locator + quote + claim_type` | 结果可核验性 |

**复核动作**：随机抽 5 个节点、3 条关系，逐字回到材料原文核对。

## 2. 使用增益（25 分）对照

| 自检项 | 判据 |
|-|-|
| 质量增益 | 抽取→建树→连线→挂证→渲染五步都有明确规则，减少遗漏与重复 |
| 效率增益 | 骨架 3 分钟内落盘；脚本确定性渲染免去手工改图 |
| 增益可验证 | `selfcheck.py` 输出 PASS/WARN/FAIL 计数，可作为公平对照记录 |

**复核动作**：记录本次运行 `selfcheck.py` 的 pass/warn/fail 数，与无 Skill 基线对比（基线由平台提供，不自行编造）。

## 3. 创新增益（15 分）对照

| 自检项 | 判据 |
|-|-|
| 新增能力 | 纯标准库 SVG+PDF+FreeMind+MD+JSON 五格式一次产出，含关系箭头与证据 note |
| 方法改进 | 确定性 DFS 槽位布局；kind/type 词表固定归类；证据 `fact/summary/inference` 三级 |
| 创新证据 | 不同长度材料、重复运行结果一致（确定性），异常输入不崩溃 |

## 4. 可靠体验（10 分）对照

| 自检项 | 判据 |
|-|-|
| 调用易用 | SKILL.md 有合法 frontmatter；相对路径 `scripts/*.py` 可直接执行 |
| 运行稳定 | 空输入、坏 JSON、坏编码、缺 contract 均退出码 0 且写合法 result.json |
| 异常处理 | 缺数据→insufficient_evidence；缺软件→degradations；缺参数→默认 /input、/app/output |

**复核动作**：本地各跑一次空目录、坏 JSON、缺输入三态，确认不崩溃。

## 5. 生态贡献（10 分）对照

| 自检项 | 判据 |
|-|-|
| 文档完整 | SKILL.md 含用途/输入输出/流程/自检/降级；references 含节点关系定义 |
| 资源可复用 | 3 个标准库脚本可独立运行；词表与模板在 schema 文档中可直接复用 |
| 分享价值 | 统一数据契约 `mindmap.json` 可作为 benchmark 与二次开发接口 |

## 6. 本地自检命令

```bash
cd "${SKILL_DIR:-/skills/weekly-skill}"
python3 -B scripts/scaffold.py --input ./demo/input --output ./demo/output --work ./demo/work
python3 -B scripts/render_mindmap.py --mindmap ./demo/work/mindmap.json --output ./demo/output
python3 -B scripts/selfcheck.py --input ./demo/input --output ./demo/output --mindmap ./demo/work/mindmap.json
```

> 空目录、坏 JSON、缺输入三种情况各跑一次；demo 目录仅用于本地验证，正式案例由平台提供。
