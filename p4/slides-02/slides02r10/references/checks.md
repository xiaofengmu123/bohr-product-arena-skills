# 校验规则与自检清单（checks）

`python3 scripts/main.py check` 与 `python3 -B scripts/selftest.py` 共用下列规则。
check 的退出码：0＝通过，1＝存在缺口；`self_check.json` 无论通过与否都会写出。

## 1. 契约校验（check）

| 检查项 | 规则 | 失败含义 |
|-|-|-|
| result.json 可解析 | 顶层为对象 | 无法交付 |
| case_id | 非空字符串 | 无法对应输入 |
| status | 属于 partial/complete/failed | 状态非法 |
| 契约字段 | output_contract 全部键存在于 result.json | 缺交付字段 |
| 主产物 | presentation_file 存在；.pptx 可作 zip 打开且幻灯片页数 > 0 | 不可打开 |
| 预览件 | preview_files 每项文件存在 | 渲染件缺失 |
| 证据映射 | evidence_map.json 存在，每页含 locator+quote | 不可复核 |
| 内容规格 | deck_spec.json 存在，页面结构合法 | 无事实源 |

## 2. 页面规格校验（spec/check 共用）

- `pages` 非空数组；每页为对象。
- 每页必有非空 `role`、`claim`、`notes`。
- `role` 属于白名单：title/hook/context/problem/motivation/gap/method/result/analysis/comparison/limitation/conclusion/takeaway/appendix/agenda/thanks。
- 每页必有 `locator` 与 `quote`；缺一记缺口。
- 每页要点 ≤ 4 条，每条 ≤ 28 字（人工/Agent 写稿时执行；脚本仅提示预算）。

## 3. 渲染件校验（pptx-info）

- 用 `zipfile` 打开 .pptx：`ppt/slides/slideN.xml` 计数为页数。
- `ppt/slideLayouts` 与 `ppt/slideMasters` 计数反映母版/版式是否保留；有母版要求但 master_count=0 时记缺口。
- 逐 `<a:t>` 读回文本、逐 `<p:ph>` 读回占位符；发现 `TODO`/`占位`/`Lorem` 记缺口。
- 文本不逐页跑版式渲染（那是 office-renderer 的职责）；字体缺失、溢出、图片裁剪用 font-discovery 与导出复查发现。

## 4. 交付前自检清单（对应评分维度，逐条勾选）

- [ ] 实际效果：可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；备注齐备；PDF/PNG 预览真实生成。
- [ ] 实际效果：页上数字、图注、引用编号与原文/数据表一致，无编造、无按印象取整。
- [ ] 实际效果：覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素。
- [ ] 实际效果：evidence_map、sources、版本快照、change_log 齐全，每项证据含 locator+quote，可独立复核复跑。
- [ ] 使用增益：同输入同模板可比；记录返工页数、重排次数、导出复查轮次作为效率证据。
- [ ] 使用增益：质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑。
- [ ] 创新增益：deck spec 先行、母版槽位填充、图表数据绑定、局部编辑有方法与边界说明。
- [ ] 可靠体验：按 SKILL.md 稳定触发；综述/实验/理论三类与不同模板可复跑、不塌版。
- [ ] 可靠体验：缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置。
- [ ] 生态贡献：说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/检查清单可复用。

## 5. 已知边界

- 本包不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- PPTX 生成/编辑由 Agent 使用 python-pptx 或脚本直接编辑 OOXML 完成，脚本负责骨架、规格、证据与终检；缺 python-pptx/soffice 时回退 Markdown/HTML 并如实标注。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试，不伪造运行结果与评测分数。

## 6. 使用增益的可验证记录

- 同一输入与母版下，留存 `work/` 内 `deck_spec.json`、`evidence_map.json`、`pptx_info.json`、`self_check.json` 的逐轮时间戳，作为质量与效率的可复核证据。
- 记三组可核对计数：返工页数（spec 中 claim/quote 被改写的页）、重排次数（pages 顺序变更）、导出复查轮次（pptx-info/check 运行次数）。
- 相对基础 Agent 的改善须用同输入、同模板、同环境的 diff 或日志对照，不接受无对照的文字自述。
- 每个版本快照保存“改了什么 + 为什么改 + 可否回退”，改坏后以 `version_records.json` 定位上一版文件路径。
