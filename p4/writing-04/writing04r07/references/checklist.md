# 提交前自检清单（逐条对应评分维度）

交付前逐条勾选；任何未勾选项必须在 `result.json` 的
`errors` / `degradations` / `pending_human` / `uncertainty` 中如实说明，
不得静默跳过。本清单与 `SKILL.md` 的“自检清单”保持一致。

## 实际效果（40 分）

- [ ] 任务完成度：从真实稿件 + 真实文献库出发，完成引用插入定位、编号
      更新、去重、格式转换、来源核验，交付可编辑稿件（`manuscript.md`）
      与同步文献表（`references.md`）。
- [ ] 结果正确性：每条引用落到真实、可定位的文献条目；元数据来自材料；
      论断与所引文献主题/结论匹配。
- [ ] 结果完整性：覆盖任务要求的动作、全部章节、全部引用点、全部文献条目。
- [ ] 结果可核验性：能从成品回溯原始稿件、文献库快照、每条引用匹配依据
      （`evidence_map.json`）与每步改动（`change_log`）。

## 使用增益（25 分）

- [ ] 质量增益：加载本 Skill 后编号连续、条目完整、引用位置正确，优于
      未加载的基线。
- [ ] 效率增益：编号重排、去重、格式转换由 `scripts/main.py` 自动完成，
      人工只做语义确认。
- [ ] 增益可验证性：同一输入重复运行结果一致（脚本确定性），可用
      `--self-test` 与同输入对照验证。

## 创新增益（15 分）

- [ ] 新增能力：编号连锁更新（正文标记 ↔ 文末列表同步）。
- [ ] 方法改进：去重判据可解释（DOI / 规范化题名 + 第一作者 + 年份），
      合并依据写入 `merge_evidence`。
- [ ] 创新证据：论断级匹配逐句记录，能说明相对基础 Agent / 通用工具的
      价值边界。

## 可靠体验（10 分）

- [ ] 调用与易用：`python3 -B scripts/main.py --help` 退出码 0；默认
      `--input /input --output /app/output --work /app/work`。
- [ ] 运行稳定性：空目录、坏 JSON、坏编码、缺字段、重复运行均不崩溃且
      产出合法 `result.json`。
- [ ] 异常处理：字段缺失、DOI 解析失败、条目歧义、编号冲突、格式不支持、
      文件损坏、超大文件均识别并写入对应字段。

## 生态贡献（10 分）

- [ ] 文档完整性：用途、适用条件、输入输出、异常处理、限制、维护信息齐全
      （见 `SKILL.md`）。
- [ ] 资源可复用：脚本纯标准库；规则与检查清单位于 `references/`；去重
      规则、格式映射可独立复用。
- [ ] 分享价值：沉淀失败模式与实践经验，不依赖作者个人文件或手工操作。

## 最终校验命令

```bash
python3 -B scripts/main.py --self-test          # 退出码 0
python3 -B scripts/selftest.py                  # 退出码 0
python3 -B scripts/main.py                      # 跑通 /input -> /app/output
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('case_id') is not None and r.get('status') in {
    'ok', 'partial', 'insufficient_evidence', 'input_error'}
print('result ok:', r['status'])
PY
```
