# 自检与评分维度对照清单

交付前逐条勾选；每条对应评分维度，勾选须有产物或日志支撑，不靠文字自述。

## A. 实际效果（40 分）

- [ ] **任务完成度（15）**　清洗、统计、可视化、执行/查询各环节均产出，且与 task.md 要求一一对应。核对：`data_profile.json` 有输入画像，`/app/work/` 有分析脚本与执行日志，`result.json` 主产物路径指向真实文件。
- [ ] **结果正确性（15）**　不存在"缺失当 0"、未去重分组、t 检验前提未查、SQL WHERE 漏条件、单位/有效数字不一致。核对：每个数值结论在 `evidence_map` 中带 locator + 逐字 quote 且 `evidence verify` 全通过。
- [ ] **结果完整性（5）**　output_contract 全部字段回填（缺值 `null`/`[]`），主产物 + 预览齐备。核对：`validate` 的 `result=ok`。
- [ ] **结果可核验性（5）**　原始快照、规则表、代码、依赖版本、随机种子、命令行、日志、退出码齐全。核对：`change_log` 记录了每一步动作与失败恢复，`derivation` 记录了方法/种子/命令。

## B. 使用增益（25 分）

- [ ] **质量增益（15）**　与基础 Agent 相比数值正确率与一次跑通率更高。核对：Step 0 先落盘 partial 骨架；Step 4 用 `evidence verify` 机器拦截"看起来对了"的静默错误。
- [ ] **效率增益（5）**　减少调试轮次、依赖排查、人工改数。核对：日志显示"先定位根因再改代码"，而非换段代码硬试。
- [ ] **增益可验证（5）**　有公平对照或可核验的运行证据。核对：运行日志 + 退出码 + `validation_report.json` 共同证明，不写"已测增益"字样。

## C. 创新增益（15 分）

- [ ] **新增能力（5）**　字段对齐、口径显式化、`evidence_map` 逐字溯源是基础方案难以稳定实现的。核对：`evidence add`/`evidence verify` 可用。
- [ ] **方法改进（5）**　确定性重跑（固定随机种子 42）、缺失/异常策略可解释。核对：同一输入两次运行结果一致。
- [ ] **创新证据（5）**　证明创新点相对已有方案的边界与价值。核对：`references/evidence_contract.md` 写清 schema 与校验闭环。

## D. 可靠体验（10 分）

- [ ] **调用与易用性（3）**　按 SKILL.md §8 示例能独立完成"给数据、出结果"。
- [ ] **运行稳定性（4）**　空输入、坏 JSON、坏编码、缺参数四种场景退出码 0 且产出合法结果。核对：`python3 -B scripts/selftest.py --output /app/work/selftest` 全 PASS。
- [ ] **异常处理（3）**　缺列、类型不符、依赖缺失、查询为空、数值溢出、权限不足均有识别/重试/降级/转人工策略。核对：`errors`/`degradations`/`limitations`/`pending_human` 字段如实记录。

## E. 生态贡献（10 分）

- [ ] **文档完整性（3）**　数据字典、字段映射、参数含义、依赖版本、示例、失败处理齐全。
- [ ] **资源可复用性（4）**　脚本仅依赖标准库，路径用相对路径，他人可直接复跑。
- [ ] **分享推广价值（3）**　沉淀了可复用的证据溯源契约与自检脚本。

## 提交前最小自检命令

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --help          # 退出码 0
python3 -B scripts/selftest.py --output /app/work/selftest   # 全 PASS
python3 -B scripts/main.py all --input /app/work/selftest/badenc_in --output /app/work/demo_out --work /app/work/demo_work
python3 - <<'PY'
import json, os
r = json.load(open('/app/work/demo_out/result.json', encoding='utf-8'))
assert r.get('case_id') and r.get('status')
print('ok', r['status'])
PY
```
