# 自检清单与降级矩阵

## 1. 逐条自检清单（提交前逐项勾选，对应评分维度）

- [ ] 六类要素全部覆盖，无空节或占位空话（结果完整性）。
- [ ] 正文每个数值/参数/版本与材料逐字一致，无补数（结果正确性）。
- [ ] 每个事实性句子有 `[S#]`，`evidence_map.json` 齐且 `locator+quote` 非空（结果可核验性）。
- [ ] `output_contract` 全部字段原样回填，`case_id`/`status`/`sources`/`uncertainty`/`change_log` 齐（输出契约）。
- [ ] 缺口显式标注：`MATERIAL GAP`、`pending_human`、`not_reported`，无静默编造。
- [ ] 冲突并列记录并标 `source_conflict`；限制条件指向具体失效场景而非套话。
- [ ] 可编辑主产物 + 渲染 PDF/PNG 预览均存在；`python3 scripts/main.py ... validate` 通过。
- [ ] `change_log` 完整记录 骨架→初稿→改进→收口，可回退。
- [ ] 台账与成稿一致：`manuscript.md` 每个 `[S#]` 都能在 `fact_ledger.json` 中找到。

## 2. 脚本自检（本地提交前）

```bash
python3 scripts/selftest.py            # 退出码 0 = 通过
python3 scripts/main.py --help         # 退出码 0
```

`selftest.py` 覆盖：正常输入、空输入目录、坏 JSON、幂等性。

## 3. 降级矩阵（诚实优先）

| 场景 | 处置 | 结果 |
|------|------|------|
| 缺材料 / 空输入 | 不生成示例答案，列出缺口 | `status=insufficient_evidence`，result.json 仍合法 |
| 坏 JSON / 坏编码 | 记录到 `errors`/`degradations`，继续处理其余材料 | `status=input_error` 或 `partial` |
| 缺软件（无 pdftotext/Office） | 调用 `pdf-text-tool`/`office-renderer`/`ocr-tool`；失败则记 `degradations` | 不影响其它材料，PDF 部分标 `MATERIAL GAP` |
| 冲突 / 无来源参数 | 标 `source_conflict` / `pending_human`，不自行裁定 | 证据映射两条都保留 |
| 时间 / 请求耗尽 | 先保证 result.json 合法 + 初稿 + 证据映射，再增量完善 | 宁交 `partial`，不交编造的 `ok` |
| 单文件 >2MB | 跳过并记录到 `degradations` | 其余材料继续 |

## 4. 预算与节奏（总 900 秒、64 次模型请求、单响应 ≤16384 tokens）

| 阶段 | 时间 | 请求 | 动作 |
|------|------|------|------|
| 第 0 步 preflight | 0–20s | 0（脚本） | 契约预检 + 落盘 result.json 骨架与六节 manuscript 骨架（**前 3 分钟必须完成**） |
| 第 1 步 inventory | 0–60s | 1–2 | 脚本扫描 + 读 state/case/task 全文 |
| 第 2 步 建台账 | 60–300s | 6–15 | 核对候选，确认后写回 fact_ledger.json |
| 第 3 步 写初稿 | 300–600s | 15–30 | 渲染 manuscript.md + evidence_map + preserved_facts |
| 第 4 步 增量改进 | 600–840s | 15–30 | 数值对齐、条件核对、缺口追问 |
| 第 5 步 validate | 840–900s | 0（脚本） | 证据后检，修正后重跑；最后 20s 只落盘 |

## 5. 作者确认追问清单模板（pending_human 复用资源）

写完初稿后，按下列模板把“材料没有、但复现必需”的缺口逐条列入 `pending_human`，每条含 `locator`（指到已写位置）与 `question`：

1. 环境/版本：`python/依赖包版本未在材料中给出，请确认 requirements.txt 或镜像 tag`。
2. 随机种子：`train/val/test 划分的 random seed 未见于配置，请确认取值`。
3. 重复次数：`未说明重复实验次数与方差报告方式（mean±sd 或单次），请确认`。
4. 数据集：`数据集版本/规模/预处理未写明，请确认下载源与版本号`。
5. 基线：`基线方法与版本未写明，请确认对比对象与是否同预算`。
6. 硬件：`训练所用 GPU/CPU 型号与显存未写明，请确认`。
7. 冲突：`[指标 X] 在 <locatorA> 与 <locatorB> 数值不一致，请确认以哪个为准`。

规则：只列**确实影响复现**的缺口，不列可留 `not_reported` 的次要细节；每条问题必须能回指正文具体位置。

## 6. 最小调用示例（示意输出，标注为示例）

> 以下为**示例**，用于说明接口与完整调用流程，不冒充正式案例结果。

输入目录（示例）：

```text
input/
├── case.json            # 含 case_id 与 output_contract
├── task.md              # 体裁、受众、格式约束
└── train_config.yaml    # lr: 0.001 / seed: 42 / epochs: 100
```

调用（示例）：

```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work preflight
python3 scripts/main.py --input /input --output /app/output --work /app/work inventory
# —— 模型读材料、建台账、写 manuscript.md / evidence_map.json / preserved_facts.json ——
python3 scripts/main.py --input /input --output /app/output --work /app/work validate
```

示意 `result.json`（示例，字段截断）：

```json
{
  "case_id": "case-demo",
  "status": "partial",
  "artifacts": ["manuscript.md", "evidence_map.json", "preserved_facts.json"],
  "sources": ["train_config.yaml"],
  "uncertainty": ["seed 未见于材料"],
  "pending_human": ["请确认 val/test 划分比例"],
  "change_log": [{"step": "preflight", "action": "..."}],
  "manuscript": {"path": "manuscript.md"}
}
```

`manuscript` 键来自 `output_contract`，示例中为占位示意；实际运行时按 `/input/case.json` 的契约原样回填。
