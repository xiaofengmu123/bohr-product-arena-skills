# 终检清单（checklist）

最终交付前逐条核对；与 SKILL.md 第 5、6 节一一对应。

## 1. 实际效果（40 分）
- [ ] 可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；备注齐备
- [ ] PDF/PNG 预览真实生成（可播放/可查看）
- [ ] 页上数字、图注、引用编号与原文/数据表一致，无编造、无按印象取整
- [ ] 覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素
- [ ] evidence_map、sources、版本快照、change_log、self_check 齐全，可复跑复核

## 2. 使用增益（25 分）
- [ ] 同输入同模板可比；记录返工页数/重排次数/复查轮次作为效率证据
- [ ] 质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑，不以文字自述代替

## 3. 创新增益（15 分）
- [ ] 页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界说明
- [ ] 输出契约字段逐一对齐 output_contract，未知键保留、缺失值显式空值，可被审计

## 4. 可靠体验（10 分）
- [ ] 按 SKILL.md 能稳定触发；综述/实验/理论三类与不同模板可复跑、不塌版
- [ ] 缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置

## 5. 生态贡献（10 分）
- [ ] 使用说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/检查清单可复用

## 6. 交付前机械核对
- [ ] `python3 scripts/main.py check --strict` 退出码为 0（或缺口已如实写入 uncertainty）
- [ ] `python3 scripts/main.py finalize --status ...` 后 result.json 顶层含 case_id + 全部契约字段 + 保留字段
- [ ] `/app/output/result.json`、`self_check.json`、可编辑源文件、PDF/PNG 预览齐备
- [ ] change_log 四阶段（scaffold/build/render/finalize）至少各一条，seq 递增
- [ ] 每页关键结论有 locator + 逐字 quote（evidence_map）；冲突来源标 source_conflict
