# 自检清单与评分维度映射

提交前逐条核对；`python -B scripts/selftest.py` 自动验证其中带 ★ 的条目。

## 1. 结果完整性 / 可核验性（实际效果 40 分）

- [ ] ★ `result.json` 含 `case_id` + 全部 `output_contract` 字段 + `status`/`sources`/`uncertainty`/`pending_human`/`change_log`。
- [ ] `graphical_abstract` 四要素（question/method/results/conclusion）齐全或明确 `null`。
- [ ] 每个 entity/relation/data/legend 有 `source` locator + 逐字 `quote`；`sources[]` 汇总唯一 locator。
- [ ] 无虚构 DOI/数值/引用；缺失项在 `uncertainty` 而非编造。

## 2. 结果正确性（实际效果）

- [ ] 实体 `kind` 与材料层级一致（分子/细胞/组织/材料/器件/样本不混淆）。
- [ ] 关系 `from`/`to`/`direction` 与原文因果方向一致（activates/inhibits/leads to）。
- [ ] 数据 `value`/`unit` 与原文一致；`encoding`（长度/颜色/面积/亮度）与数值一一对应。
- [ ] 图例 swatch 颜色与 data/entity 编码一致；单位、条件、比例尺标签完整。

## 3. 可编辑源文件 / 导出件（实际效果 + 可靠体验）

- [ ] SVG 分层命名（`layer-background/title/panels/entities/relations/data/legend/annotations`）。
- [ ] 文字保持 `<text>` 可编辑，无 text-as-path；图元带 `data-source`/`data-id`。
- [ ] PNG/PDF 是真实文件；无渲染器时 `exports[].status=preview_not_rendered`，不伪造。

## 4. 版式与视觉层级（可靠体验）

- [ ] 无画布溢出（元素坐标在 viewBox 内）。
- [ ] 字号 ≥8 px；正文/标题层级清晰；阅读顺序「问题→方法→结果→结论」。
- [ ] 颜色对比度达标；色觉差异与灰度打印下可分离（固定色板保证）。

## 5. 运行稳定性 / 可复核（可靠体验 4 分 + 使用增益 5 分）

- [ ] ★ 同一输入两次运行，`result.json` 与 `fig_*.svg` 字节一致。
- [ ] `determinism.fingerprint` 稳定且存在；运行时遥测只进 `/app/work/run_meta.json`。
- [ ] `change_log` 每次改写 seq 递增，能还原「骨架→抽取→建图→校验→定稿」。
- [ ] ★ `selftest.py` 4 项（determinism/contract/empty_dir/bad_json）全部通过。

## 6. 异常处理（可靠体验 3 分）

- [ ] 缺数值 → `null` + `uncertainty`；歧义 → 拆分 id 或无向连接并注明。
- [ ] 缺 case.json → `failed` 骨架退出码 1；坏 JSON/坏编码 → `errors`/`degradations` 记录，不崩溃。
- [ ] 同一策略失败两次即停止重试，`partial` 收尾并写 `limitations`。

## 7. 生态贡献 / 复用性（生态贡献 10 分）

- [ ] 脚本相对路径 `scripts/*.py`、纯标准库、`main.py < 30KB`。
- [ ] `references/output_schema.md` 讲清 schema；本文件讲清清单。
- [ ] 无隐藏个人文件；最小调用示例标注为示例。

## 快速自检命令

```bash
cd /skills/weekly-skill
python -B scripts/main.py --help          # 退出码 0
python -B scripts/selftest.py             # 4/4 通过
python -B -m py_compile scripts/*.py      # 语法通过
```
