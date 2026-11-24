# 自检与评分维度映射

交付前逐条核对。每条标注对应评分维度与分值项，以便在固定案例运行时定向查漏。

## 1. 实际效果（40 分）

- [ ] 任务完成度（15）：理解导图用途、目标读者、材料范围与交付要求；`deliverable` 含可打开、可读、可继续编辑的 OPML + Markdown + SVG 预览，`meta.purpose/audience` 已填。
- [ ] 结果正确性（15）：节点忠于原文；关系方向正确；证据挂在对应节点；无材料中不存在的连接（`relations` 每条有 evidence）。
- [ ] 结果完整性（5）：覆盖问题、方法、证据、结论、任务、风险及必要层级；`version_boundaries.node_count/relation_count` 记录规模。
- [ ] 结果可核验性（5）：`evidence_map` 每条含 `locator + quote + source + kind + verified`，可逐条回溯。

运行：`python3 -B scripts/main.py --check --input /input --output /app/output` 应返回 `ok:true`。

## 2. 使用增益（25 分）

- [ ] 质量增益（15）：同一材料下 OPML 可直接导入导图工具继续编辑，SVG 确定性布局减少手工改图；证据 `verified` 自动核对减少漏挂/错挂。
- [ ] 效率增益（5）：`runtime.seconds_elapsed` 记录耗时；一次 finalize 即产出三格式，减少重复操作与返工。
- [ ] 增益可验证性（5）：`change_log` 记录 scaffold→finalize 增量；`--check` 输出可复现的问题清单，便于公平对照。

## 3. 创新增益（15 分）

- [ ] 新增能力（5）：`verified` 逐字核对 + `source_conflict` 自动检测，是基础 Agent 难以稳定实现的证据可追溯能力。
- [ ] 方法改进（5）：固定节点/关系词表 + 确定性树布局 + 证据挂接分离，避免层级混乱与关系乱连。
- [ ] 创新证据（5）：`uncertainty` 记录冲突、`pending_human` 记录未命中 quote，形成可核验的改进边界。

## 4. 可靠体验（10 分）

- [ ] 调用易用（3）：`scripts/main.py` 缺省指向 `/input`、`/app/output`、`/app/work`，零配置可跑；`--help` 退出码 0。
- [ ] 运行稳定（4）：不同材料类型/长度、重复运行均产出可读导图；确定性布局（无随机种子依赖）。
- [ ] 异常处理（3）：空输入→`insufficient_evidence`；坏 JSON/坏编码→`degradations`；规模爆炸→截断并注明；权限不足→`errors` 记录，均不崩溃。

## 5. 生态贡献（10 分）

- [ ] 文档完整（3）：SKILL.md 含角色、输入输出契约、执行流程、自检、降级；references 定义节点/关系/证据词表。
- [ ] 资源可复用（4）：脚本仅标准库、相对路径；OPML/MD/SVG 及 spec 模板可被他人直接复用。
- [ ] 分享价值（3）：spec schema + 证据词表 + 自检清单可沉淀为他人整理方法的起点。

## 6. 常见失败模式（提交前必查）

| 失败 | 症状 | 修复 |
|-|-|-|
| 未落盘骨架 | 异常时无 result.json | 第 0 步先跑 `main.py` scaffold |
| 输出契约键缺失 | 评分器找不到 deliverable/evidence_map | 回填 `output_contract` 全部键 |
| quote 非逐字 | `verified=not_found` 或 `inference` 冒充 `fact` | 改写为原文片段或标 `inference` |
| 关系无据 | relation 无 evidence | 删除或补 evidence |
| 层级交叉 | 同级混有概念+结论 | 按六维重新归位 |
| 编造状态 | status=ok 但材料不足 | 如实降级为 partial/insufficient_evidence |

任何自动化结果不得伪造：`result.json` 的 `status`、`verified`、`errors` 必须由脚本或实际核对得出。
