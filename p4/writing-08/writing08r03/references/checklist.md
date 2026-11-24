# 六要素覆盖表与模板

本文件是 `SKILL.md` 第 5–7 节的展开：六要素覆盖表、参数表模板、证据映射模板、自检清单与追问清单。全部为复用模板，不含正式案例答案。

## 1. 六要素覆盖表（结果完整性）

| 要素 | 必写项 | 常见漏写（扣分点） |
|-|-|-|
| 方法步骤 | 每步 输入→操作→输出→判断条件；步骤编号可执行 | 把多步压成一句；无输入/输出；无停止条件 |
| 实验设置 | train/val/test 分开；硬件、软件版本、环境、随机种子、重复次数 | 训练与评测条件串台；只写"默认参数" |
| 参数与来源 | 名称、取值、单位、作用对象、来源（配置/日志/作者确认） | 正文数值与表不一致；来源缺失 |
| 数据集与划分 | 名称、版本、规模、预处理、划分比例、随机方式、各子集用途 | 版本不明却当已确认；不写划分随机方式 |
| 对照实验 | 基线名称与版本、是否同预算、评价指标、统计方式 | "与基线相当"而无数字/统计 |
| 复现与限制 | 代码版本、环境、命令、数据获取方式；限制指向具体失效场景 | 复现清单缺命令/种子；限制写套话 |

## 2. 参数表模板

```markdown
| 参数 | 取值 | 单位 | 作用对象 | 来源 | 证据 |
|-|-|-|-|-|-|
| learning_rate | 3e-5 | - | optimizer | config.yaml#L3 | [S1] |
| batch_size | 16 | 样本/步 | DataLoader | train.log#L2 | [S2] |
```

规则：取值逐字复用，不改单位与有效数字；来源写 locator；证据列挂 `[S#]` 指回 `evidence_map.json`。材料未给的值填 `not_reported`，正文同样写 `not_reported`，禁止临时补数。

## 3. 证据映射模板（evidence_map.json）

```json
{
  "S1": {
    "claim": "学习率为 3e-5",
    "locator": "materials/config.yaml#L3",
    "quote": "learning_rate: 3e-5",
    "status": "verified"
  },
  "S2": {
    "claim": "验证集准确率 0.872",
    "locator": "materials/train.log#L5",
    "quote": "val loss 0.411 acc 0.872",
    "status": "verified"
  }
}
```

`status` 取值：`verified`（有出处）、`source_conflict`（两处不一致，另加 `conflict_with` 数组）、`not_reported`（材料缺失）、`pending_human`（需作者确认）。locator 与 quote 必须非空。

## 4. 自检清单（逐条勾选，对应评分维度）

- [ ] 六类要素全部覆盖，无空节或占位空话（结果完整性 5 分）。
- [ ] 正文每个数值/参数/版本与材料逐字一致，无补数（结果正确性 15 分）。
- [ ] 每个事实性句子有 `[S#]`，`evidence_map.json` 齐且 locator+quote 非空（结果可核验性 5 分）。
- [ ] `output_contract` 全部字段原样回填，`case_id`/`status`/`sources`/`uncertainty`/`change_log` 齐（输出契约）。
- [ ] 缺口显式标注 `MATERIAL GAP`/`pending_human`/`not_reported`，无静默编造（结果正确性）。
- [ ] 冲突并列记录并标 `source_conflict`；限制条件指向具体失效场景而非套话。
- [ ] 可编辑主产物 + 渲染 PDF/PNG 预览均存在；`validate` 通过。
- [ ] `change_log` 用步骤标签（skeleton→inventory→draft→improve→finalize）而非时间戳，完整可回退。
- [ ] `content_sha256` 由 `finalize` 重算；`python3 scripts/selftest.py` 退出码 0（确定性）。

## 5. 作者追问清单（pending_human 模板）

按"具体、可单选、不替作者决定"组织：

1. 数据集版本：材料出现 `dataset v1` 与 `v2`，请确认评测用的是哪个。
2. 随机种子：train 脚本 seed=42，log 中未见 eval 种子，请确认评测是否复用 42。
3. 划分比例：README 写 8:1:1，脚本写 80/10/10，请确认有效数字与取整方式。
4. 基线预算：对照实验是否与主方法同预算（同 epoch/同 GPU 时），请确认。
5. 硬件型号：材料只写 "A100"，请确认显存规格与驱动/CUDA 版本。

每个追问挂到 `pending_human` 数组，并给出触发它的 locator；不要把它写成结论。
