# 自检与验证清单

交付前逐条执行；每条对应题面评分维度。命令均从 Skill 包根目录运行，用 `python3 -B`。

## 1. 结构自检（结果正确性 / 完整性 / 可核验性）
```bash
python3 -B scripts/evidence_check.py \
  --input /app/work/mindmap.json \
  --materials-dir /input \
  --report /app/output/evidence_report.json
```
- [ ] 无重复节点 id、无父节点自引用、无环。
- [ ] 所有边引用的节点存在；事实承载节点都有证据或已标 `inferred`。
- [ ] 证据项 locator+quote 齐全；locator 指向的文件在材料目录中存在。
- [ ] 冲突已被报告（`source_conflict`）并写进 result.json 的 uncertainty。

## 2. 产物自检（可打开 / 可编辑）
```bash
python3 -B scripts/mindmap_render.py \
  --input /app/work/mindmap.json \
  --output-dir /app/output
python3 - <<'PY'
import xml.etree.ElementTree as ET
ET.parse('/app/output/mindmap.svg')
ET.parse('/app/output/mindmap.mm')
print('svg/mm ok')
PY
```
- [ ] `mindmap.svg` 可被 XML 解析；`mindmap.mm` 可被 XML 解析；`outline.md` 可读。
- [ ] 若平台提供渲染工具，已生成 `mindmap.pdf`/`mindmap.png`；否则 limitations 已说明。

## 3. result.json 自检（输出契约）
```bash
python3 -B scripts/result_builder.py --validate --result /app/output/result.json
```
- [ ] 含 `case_id`；`status` 为 ok/partial/insufficient_evidence/input_error 之一。
- [ ] 回填了 output_contract 的全部键；附加 `sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`runtime`。
- [ ] 不伪造 benchmark 分数、DOI、数值；未知项写 not_reported / pending_human。

## 4. 稳健性自检（异常处理 / 稳定性）
按顺序各跑一次，进程不得崩溃：
```bash
# 空输入目录
python3 -B scripts/result_builder.py --init --case /app/work/nonexistent.json --output-dir /app/output
# 坏 JSON
printf 'not json' > /app/work/bad.json
python3 -B scripts/result_builder.py --init --case /app/work/bad.json --output-dir /app/output
# 缺参数：缺省 /input、/app/output、/app/work
python3 -B scripts/result_builder.py --init
```
- [ ] 空输入返回 `insufficient_evidence` 或 `input_error`，退出码如实（init 仍写合法骨架）。
- [ ] 坏 JSON/坏编码被捕获并写入 errors/degradations，其余材料继续处理。
- [ ] 重复运行同一输入，除 `runtime` 时间戳外结果一致（渲染确定性）。

## 5. 诚实性自检
- [ ] 未确认内容在 pending_human，缺口在 limitations，冲突在 uncertainty。
- [ ] 没有为凑结构而示例填充或复制大段题面；示意输出已标注为示例。
- [ ] status 与真实材料一致：材料不足即 insufficient_evidence，不写 ok。

## 6. 交付前最终检查
- [ ] SKILL.md 含 name/description/version 的 YAML frontmatter；脚本路径为相对路径 `scripts/xxx.py`。
- [ ] 总文件数 ≤14、总体积 ≤1.2MB；脚本仅依赖 Python 3 标准库。
- [ ] 不在 Skill 包内放正式答案、白板或预写测试产物。
