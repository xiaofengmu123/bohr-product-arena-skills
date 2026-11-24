# 检查清单与评分维度对齐（checklist）

与 SKILL.md 逐项对应；交付前逐条勾选。每条标注对应评分维度与可核验产物。

## 1. 实际效果（40 分）
- [ ] 可编辑演示文件存在且可打开；页数/页序符合约束；每页有 role+claim；讲稿备注齐备 → `content_map` / `presentation.pptx`
- [ ] PDF/PNG 预览真实生成（可播放/可查看）→ `presentation.pdf` / `export_artifact`
- [ ] 页上数字、图注、引用编号与原文一致，无编造/取整 → `evidence_map.json`（quote_verified）
- [ ] 覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素 → `compliance_checks`
- [ ] evidence_map、sources、版本快照、change_log、self_check 齐全，可复跑复核 → `result.json`

## 2. 使用增益（25 分）
- [ ] 同输入同模板可比；记录返工页数/重排次数/复查轮次 → `change_log` / `runtime`
- [ ] 质量与效率改进有 diff/日志/对照支撑 → `report.md` / `changes.json`

## 3. 创新增益（15 分）
- [ ] 页序规划、模板槽位填充、图表数据绑定、局部编辑有方法与边界说明 → `dimension_map.json`
- [ ] edit 只改目标页，其余页与母版不变 → `compliance_checks.edit_scoped` / `changes.json`

## 4. 可靠体验（10 分）
- [ ] 稳定触发与调用；综述/实验/理论三类与不同模板可复跑、不塌版 → `selftest.py --demo`
- [ ] 缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有处置 → `uncertainty` / `degradations`

## 5. 生态贡献（10 分）
- [ ] 说明、依赖、参数、页数限制、已知问题齐全 → `SKILL.md` / `references/contract.md`
- [ ] 脚本/骨架/检查清单可复用 → `scripts/main.py` / `scripts/core_impl.py` / `scripts/selftest.py`

## 6. 终检脚本
```bash
python3 scripts/main.py scaffold   # 落骨架
python3 scripts/main.py audit      # 校验草稿
python3 scripts/main.py build      # 生成产物
python3 scripts/main.py finalize   # 写 selfcheck.json + dimension_map.json
python3 scripts/selftest.py        # 退出码 1 = 有缺口
```

## 7. 已知边界
- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；PPTX 由运行时已装工具（python-pptx/office-renderer）完成，脚本负责骨架、记录与终检。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。
