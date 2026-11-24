# 自检与交付检查清单

提交/交付前逐条勾选；`python scripts/selftest.py` 自动化其中第 1、3、4、5 条的机器
部分。以下每条对应 task.md 的评分维度。

## A. 结果完整性与可核验性

- [ ] `result.json` 存在且为合法 JSON（utf-8）。
- [ ] 含 `case_id`；`case.json` 的 `output_contract` 每个字段都已回填，缺值 `null`/`[]`。
- [ ] 含 `status / sources / uncertainty / pending_human / change_log / limitations / runtime`。
- [ ] `sources` 每项含 `locator` + 逐字 `quote`；图中出现的每个数值都能指回一条 source。

## B. 实体关系与数据映射正确

- [ ] 每个框/箭头/条形/图例色块映射到一条 entity/relation/data，各带 `sources` 指针。
- [ ] 箭头方向与来源句的因果/流向一致；抑制类关系用 T-bar 而非普通箭头。
- [ ] `data_mapping` 中每个数据项给出 channel（length/color/dot）+ visual_item + value + unit。

## C. 可编辑源文件与导出件

- [ ] SVG 存在、可被 XML 解析、根为 `<svg>`。
- [ ] 含命名 `layer-*` 分组；文字是 `<text>`（无 text-as-path）；元素带
      `data-source`/`data-ref`/`data-channel`。
- [ ] PNG/PDF 若标 `rendered`，文件真实存在且以正确魔数开头（`\x89PNG` / `%PDF-`）；
      否则如实标 `preview_not_rendered` 并写 limitation。

## D. 版式与视觉层级

- [ ] 无画布溢出（元素 x/y/w/h 不越界）。
- [ ] 字号 ≥8 px（默认 ≥10 px）；长标签已换行/截断并标注。
- [ ] 灰度/色盲分离已检查：正文信息不单靠红绿区分，且搭配文字/图案。

## E. 运行稳定性与可复核

- [ ] 相同输入 + 相同 seed 重跑 build，SVG 字节一致（figure_model 确定性）。
- [ ] `plan.json` 与 `figure_model.json` 均在 `/app/work/`，可独立 diff。
- [ ] 每次改写 bump `change_log`；脚本自相对路径 `scripts/*.py` 运行、纯标准库。

## F. 异常处理

- [ ] 缺失数值 → 字段 `null` + `uncertainty`；实体歧义 → 拆 id 或标无向连接。
- [ ] 缺 case.json → `status=failed` 并记录，不继续编造。
- [ ] 坏 JSON/坏编码/超 2MB 材料 → 捕获并记 limitations，继续处理其余材料。

## 最小调用示例（接口演示，非正式案例答案）

```bash
cd /skills/weekly-skill
python scripts/main.py init                 # 落盘 partial 骨架（前 3 分钟内）
# Agent 编辑 /app/work/plan.json 填入实体/关系/数据/来源
python scripts/main.py build                # 生成 figure_model + SVG + 尝试 PNG/PDF
python scripts/main.py validate             # 校验并更新 result.json
python scripts/selftest.py                  # 包级自检
```

示意输出（务必标注为示例）：`fig_demo-case-0001.svg` + `result.json`（status=completed）。
正式案例结果由平台固定案例生成，不以本示例冒充。
