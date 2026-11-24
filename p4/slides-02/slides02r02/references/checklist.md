# 检查清单与降级表（checklist）

与 SKILL.md 第 5 节逐条对应；最终交付前逐条勾选。`python3 scripts/selftest.py check` 只做可自动化的部分校验，以下清单覆盖评分维度的语义项，需由 Agent 结合产物逐一确认。

## 1. 终检清单（按评分维度）

实际效果（40 分）
- [ ] 可编辑演示文件存在且可打开；页数/页序符合 case 约束；每页有明确叙事角色；讲稿/备注齐备
- [ ] PDF/PNG 预览由真实渲染生成（非伪造），可查看/播放
- [ ] 页上数字、图注、引用编号与论文原文/数据表逐字一致，无编造、无按印象取整
- [ ] 覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素
- [ ] `evidence_map`、`sources`、`version_records`、`change_log`、`self_check` 齐全，他人可独立复跑

使用增益（25 分）
- [ ] 同输入同模板可比；记录返工页数、重排次数、导出复查轮次（写进 change_log/version_records）
- [ ] 质量与效率改进有 diff/运行日志/公平对照支撑，不以文字自述代替

创新增益（15 分）
- [ ] 页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界说明
- [ ] 说明创新点相对基础 Agent / 开源方案 / 商用插件的边界与价值

可靠体验（10 分）
- [ ] 能按本文稳定触发与调用；综述/实验/理论三类、不同模板、重复运行不塌版
- [ ] 缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置

生态贡献（10 分）
- [ ] 使用说明、依赖、参数、页数限制、已知问题齐全
- [ ] 脚本/骨架/检查清单可复用；`selftest.py selftest` 可在无网络环境跑通

## 2. 自动检查（selftest.py check）

必查项（失败则退出码 1）：`result.json` 合法、`case_id` 非空且非 unknown、`status` 合法、`change_log` 非空且时间升序、`sources` 为数组、`uncertainty` 为对象、`pending_human` 为数组、契约字段键存在、`runtime.scaffold_t` 存在（早落盘审计）。
complete 额外必查：主演示文件存在、`page_plan/evidence_map/preview_files` 非空。
提示项（不改变退出码）：主文件存在性（partial 下）、溢出/占位扫描结果等。

## 3. 降级决策表

| 情形 | 处置 | 结果标注 |
|-|-|-|
| 论文 PDF 缺失/无正文 | 只用可确证材料，缺页标 limitation，不补造段落 | `uncertainty.missing_artifacts` + `degradations` |
| 图缺失或纯位图无法取数 | 引用原图并给准确定位（图号/页码），不重绘假曲线、不补值 | `sources` 标 kind=figure，`uncertainty` |
| 模板槽位不足 | 映射到最近似版式，保留母版/版式，记录偏差 | `uncertainty` |
| 字体缺失 | 用 font-discovery 选最接近替换，不宣称字体一致 | `uncertainty` |
| 页面溢出 | 先删要点；字号不低于 16pt；仍不行则拆页 | `change_log` |
| 导出失败 | 交付可编辑源文件 + 已成功部分预览 | `status=partial` + 失败原因/已试命令 |
| 缺 python-pptx/soffice | 回退可编辑 Markdown/HTML，不伪造“渲染成功” | `status=partial` |
| 数字/事实不确定 | 不进页面主张，逐字 quote 缺失则不写 | `uncertainty`/`pending_human` |
| 空输入目录 | 退出码 0，写 `insufficient_evidence` | `status=insufficient_evidence` |
| 坏 JSON / 坏编码 | 捕获记入 `errors/degradations`，继续处理其余材料 | `errors`/`degradations` |

## 4. 已知边界

- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；PPTX 生成由运行时已装工具（python-pptx / office-renderer）完成，脚本负责骨架、增量记录、可渲染性代理检查与终检。
- `pptx` 子命令是可渲染性**代理**（zip 结构 + 页数 + 占位符 + 字符溢出启发式），不替代真实 PDF/PNG 导出；真实导出件仍由 office-renderer 产出并进 `preview_files`。
- 请求/耗时计数器为辅助记账，平台原生 trace 为权威；冲突以平台为准。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。
