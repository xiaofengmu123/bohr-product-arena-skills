# 检查清单与降级决策（checklist）

与 SKILL.md 第 6/7 节逐项对应；最终交付前逐条核对。`scripts/selftest.py` 负责脚本层的机器自检，本清单负责评审维度的逐条勾选。

## 1. 终检清单（按评分维度）

实际效果（40）
- [ ] 可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；备注齐备
- [ ] PDF/PNG 预览真实生成（可播放/可查看）
- [ ] 页上数字、图注、引用编号与原文一致，无编造/取整（check-quotes 通过）
- [ ] 覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素
- [ ] plan/figure_model/evidence_map/version_records/self_check 齐全，可复跑复核

使用增益（25）
- [ ] 同输入同模板可比；记录返工页数/重排次数/导出复查轮次
- [ ] 质量与效率改进有 diff/运行日志/公平对照支撑，不以文字自述代替

创新增益（15）
- [ ] 页序规划、模板槽位填充、图表数据绑定、局部编辑有方法与边界说明（见 schema.md）
- [ ] 结构化中间产物（plan/figure_model/evidence_map）先于成稿落盘，实现逐页可复核

可靠体验（10）
- [ ] 稳定触发与调用；综述/实验/理论三类论文、不同模板、重复运行不塌版
- [ ] 缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置

生态贡献（10）
- [ ] 使用说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/清单可复用
- [ ] schema 文档可让第三方独立解析中间产物并复核

## 2. 脚本层自检

```bash
python3 -B scripts/main.py --help          # 退出码 0
python3 -B scripts/selftest.py             # 5 项用例全通过，退出码 0
```

`selftest.py` 覆盖：happy path 全流程、空输入、坏 JSON、quote 不匹配、缺 pptx。全部用系统临时目录的合成输入，不依赖预写正式案例。

## 3. 降级决策表

| 情形 | 处置 | 结果标注 |
|-|-|-|
| 论文 PDF 缺失 | 只用可确证材料 | `limitation` |
| 图缺失/位图无数据 | 引用原图+定位，figure_model 标 `bitmap_no_data`，不重绘不补值 | `uncertainty` |
| 模板槽位不足 | 映射最近似版式 | `uncertainty` |
| 字体缺失 | font-discovery 选替代字体 | `uncertainty` |
| 页面溢出 | 删要点/拆页，字号≥16pt | `change_log` |
| 导出失败 | 交付源文件+部分预览 | `status=partial` + 失败原因 |
| 缺 python-pptx/soffice | 回退 Markdown/HTML | `status=partial`，不伪造渲染 |
| 数字/事实不确定 | 不进页面主张 | `uncertainty` / `pending_human` |
| quote 不匹配 | 修正 quote 或降级为 pending | `quote_check` + `change_log` |

## 4. 已知边界

- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；PPTX 生成由运行时已装工具（python-pptx/office-renderer）完成，脚本负责骨架、记录、逐字核对与终检。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。
- 单文件文本读取上限 2MB；超限跳过并写入 `degradations`。
