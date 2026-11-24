# 评分维度映射、字段级契约与失败处理

## 1. 评分维度 → 本包落点

| 评分维度（100 分） | 本包落点 |
|---|---|
| 实际效果 40（完成度15/正确性15/完整性5/可核验5） | 完整可编辑改稿 + 事实/数据/引用一致 + 关键信息全保留 + evidence_map/change_log 逐段可复核 |
| 使用增益 25（质量15/效率5/可验证5） | 证据候选抽取 + 论断强度机械拦截 + 术语表直接复用，减少事实漂移与核对成本 |
| 创新增益 15（新增5/方法5/证据5） | "受众画像→取舍策略→边界标注→多文体映射"链条；强度分级、术语锁定、保留优先级、逐段对照 |
| 可靠体验 10（易用3/稳定4/异常3） | `init` 先落骨架、异常恒退出码 0、确定性排序、异常诚实标注 |
| 生态贡献 10（文档3/复用4/分享3） | SKILL.md + genre_rules + checklist + 可复用脚本与术语表格式 |

## 2. result.json 字段级契约

| 字段 | 要求 |
|---|---|
| `case_id` | 从 `/input/case.json` 原样回填 |
| `status` | `answered\|partial\|insufficient_evidence\|input_error`，诚实 |
| `output_contract` 全部键 | 原样回填、不改名、不删未知键；缺值给 `null`/`[]` |
| `sources` | 每项 `source_id/rel_path/path/kind/suffix/size` |
| `uncertainty` | 不确定项数组（与 `uncertainties` 语义一致） |
| `change_log` | 每次删改的改前/改后/理由/定位 |
| `errors` / `degradations` | 输入错误与降级说明 |
| `pending_human` / `limitations` | 待人工确认与限制 |
| `runtime` / `coverage` | 运行信息与覆盖统计（分数/模型请求由平台记录，脚本不伪造） |

主交付必须可编辑（`.md`/`.json`/`.csv`/`.tex`/`.docx`）；Office/图需另交真实 PDF/PNG 预览。

## 3. 失败与降级处理

| 场景 | 处置 |
|---|---|
| 空输入目录 | 退出码 0，`status=insufficient_evidence`，`uncertainty` 列缺口 |
| 坏 JSON / 坏编码 | `status=input_error`，`errors` 记录，继续处理其余材料 |
| 单文件超 2MB | 跳过并记 `degradations` |
| 缺 pdftotext/office 渲染 | 纯文本降级，`degradations` 注明未渲染，不伪造软件状态 |
| 数据自相矛盾 | 两来源都记录并标 `source_conflict`，不自行取舍 |
| 材料缺引用/数据 | `pending_human` / `not_reported`，禁止编造 |
| 脚本不可用 | 手写合法 result.json（逐键回填 + `partial`/`input_error`），永不交零产物 |

## 4. 验收口径（交付前逐条过）

- [ ] `result.json` 可 `json.load` 解析，含 `case_id` + 契约全部键 + `status/sources/uncertainty/change_log`。
- [ ] 主交付文件存在且非空、可编辑。
- [ ] 每个事实性论断能按 `source_id + locator + quote` 回到原文。
- [ ] 数值/单位/样本量/比较基准/图表编号/引用与源文逐条一致。
- [ ] 论断强度未升级（相关→因果、初步→定论等）。
- [ ] 术语全文一致；缩写首次展开。
- [ ] 删改均有 `change_log` 理由；缺口显式标注。
- [ ] 同一输入两次 `build` 业务内容 hash 一致（`python scripts/main.py selfcheck`）。

## 5. 最小调用示例（示意，非正式案例结果）

```bash
cd /skills/weekly-skill
python scripts/main.py init --input /input --output /app/output --work /app/work
python scripts/main.py show          # 读源文（带 para.N locator）
# ……把改写写进 /app/work/draft.json（字段见 SKILL.md 第 5 节）……
python scripts/main.py check
python scripts/main.py build
python scripts/main.py selfcheck     # 离线自检，不联网
```

示意 result.json 骨架（键以 `/input/case.json` 的 `output_contract` 为准，此处仅为接口示意）：

```json
{
  "case_id": "example-case",
  "status": "partial",
  "result": {},
  "claims": [],
  "uncertainties": ["示例：机制归因有待验证"],
  "sources": [{"source_id": "S1", "rel_path": "paper.md", "kind": "text"}],
  "uncertainty": ["示例：机制归因有待验证"],
  "change_log": [{"id": "R1", "action": "rewrite", "original_quote": "…", "revised_quote": "…", "reason": "…", "locator": "paper.md#para.3"}],
  "errors": [], "degradations": [], "pending_human": [], "limitations": [],
  "runtime": {"skill_version": "1.0.0"},
  "coverage": {"deliverable_chars": 0}
}
```
