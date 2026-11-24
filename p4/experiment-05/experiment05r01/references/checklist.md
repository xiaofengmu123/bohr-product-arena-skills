# 定稿前自检清单（对应评分维度）

每条按“检查项 → 判定方法 → 对应评分点”组织。定稿前逐条勾选，问题写进 `limitations`/`uncertainty`/`pending_human`。

## A. 实际效果（40 分）

- [ ] **五段完整**：`interpretation` 的 trends/anomalies/possible_causes/verification_steps/followup_experiments 全部非空，或如实写空并标注原因。（任务完成度）
- [ ] **趋势正确**：每条趋势写了方向、区间、对照、拟合方式、幅度与单位；非“读数复述”。（结果正确性）
- [ ] **异常定位正确**：异常点绑定到样本/批次/通道/孔位/时间点；写明观察值、期望值、偏离幅度、阈值方法与数值、是否超阈值。（结果正确性）
- [ ] **覆盖完整**：覆盖全部指标/对照/重复/边界，未解释部分写进 `limitations` 或 `pending_human`。（结果完整性）
- [ ] **可复核**：每条结论带 `evidence`（locator + 逐字 quote）；`evidence_map.json` 覆盖统计 `claims_without_evidence` 为空。（结果可核验性）

## B. 使用增益（25 分）

- [ ] **质量稳定**：结论阈值来自真实画像（data_profile.json 的 z-score/IQR/MAD），而非主观目测。（质量增益）
- [ ] **减少返工**：复核步骤是可直接执行的 target/判据，第二人无需重读全部材料即可复算。（效率增益）
- [ ] **可验证**：`change_log` 记录骨架→画像→结论→证据→定稿全过程；`runtime` 含耗时；重复运行结果一致（无随机依赖）。（增益可验证性）

## C. 创新增益（15 分）

- [ ] **绑定维度**：异常点绑定到批次/通道等维度，而非笼统“第 n 个点”。（新增能力）
- [ ] **原因排序**：`possible_causes` 按证据强度/可检验性排序，每条有支持/排除证据。（方法改进）
- [ ] **证据映射细粒度**：evidence_map 含 claims→evidence 归属、conflicts、coverage 三块，比“每结论一个 evidence 数组”更细。（创新证据）

## D. 可靠体验（10 分）

- [ ] **正确触发**：SKILL.md frontmatter 有 name/description/version；调用脚本相对路径有效。（调用与易用性）
- [ ] **多形态稳定**：CSV/TSV/JSON 直接画像；XLSX/MAT/PDF 标记 needs_export 并给出导出建议。（运行稳定性）
- [ ] **异常处置**：缺数据 → `insufficient_evidence`；坏 JSON/坏编码 → `errors`/`degradations`；单位不一致/超量程/对照缺失/重复不足 → `uncertainty`/`limitations`；结论冲突 → `source_conflict`。（异常处理）

## E. 生态贡献（10 分）

- [ ] **文档完整**：输入格式、字段含义、判定阈值、五段输出定义、依赖与限制齐全（见 references/output_contract.md）。（文档完整性）
- [ ] **可复用**：阈值配置、复核清单模板、evidence_map 结构可被他人直接复用。（资源可复用性）
- [ ] **经验沉淀**：references 记录三种真实形态（弯曲曲线/ML 计算实验/多 sheet 药典+论文）的判读经验。（分享推广价值）

## 提交前最终检查

```bash
python -B scripts/selftest.py            # 退出码 0
python -B scripts/main.py --help         # 退出码 0
python -B scripts/main.py validate --input /app/output/result.json   # 退出码 0
```

- 不预置正式案例答案、不预写正式测试产物。
- 不编造评测分数、机理、重复数或运行结果；无法判断写 `not_reported`/`pending_human`。
