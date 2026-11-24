# 思维导图 Skill：执行与自检清单

按顺序执行；每步后勾选。总预算 900 秒、64 次模型请求；**前 3 分钟必须已落盘 result.json 骨架**，之后只做增量改进。

## 一、执行步骤（时间预算）

- [ ] **1. 初始化（0–3 分钟，1–2 次请求）**
  运行 `python3 scripts/mindmap.py init --input /input --output /app/output --work /app/work`。
  产出 `/app/work/manifest.json`（输入清单）与 `/app/output/result.json`（骨架，含 case_id + 全部契约字段 + status）。
- [ ] **2. 读材料（3–15 分钟）**
  读 `manifest.json` 与 `/input/task.md`；按 manifest 逐文件读原文；PDF 用平台 pdf-text-tool 提取，**读实际文件，不凭标签脑补**。
- [ ] **3. 起草导图（15–25 分钟，10–20 次请求）**
  写 `/app/work/mindmap_draft.json`：根节点 + 层级节点 + 关系 + 证据（locator + 逐字 quote）。见 `references/schema.md`。
- [ ] **4. 渲染（1 次请求）**
  运行 `python3 scripts/mindmap.py render --mindmap /app/work/mindmap_draft.json --output /app/output --status partial`。
  产出 `mindmap.json` / `mindmap.md` / `mindmap.svg` 并更新 `result.json`。
- [ ] **5. 增量改进（剩余时间，≤40 次请求）**
  复核节点覆盖、关系方向、证据挂接；补充 `sources` / `uncertainty` / `pending_human`；改完再 render；最后把 `--status` 定为 `ok`（材料齐全且无未决项）或 `partial`。
- [ ] **6. 自检（≤2 次请求）**
  运行 `python3 scripts/selfcheck.py --output /app/output --strict`；逐条修复直至通过。

## 二、自检清单（对应评分维度）

**实际效果（40）**
- [ ] 有可打开、可读、可继续编辑的导图（`mindmap.md` 可改、`mindmap.svg` 可看可改）。
- [ ] 节点忠于材料：无材料中不存在的概念、数字、结论。
- [ ] 关系方向正确、类型合法（`schema.md` 的 relation 集合），无无依据连接。
- [ ] 覆盖关键概念/问题/方法/证据/结论/任务/风险，层级合理、同级粒度一致。
- [ ] 关键节点与关系都有证据（locator + 逐字 quote）。

**使用增益（25）/ 创新增益（15）**
- [ ] 证据挂接、确定性布局、契约回填、诚实状态是脚本保证的稳定能力，非一次性手工产物。
- [ ] 一次 init 即出骨架，增量 render 可重复运行，输出确定。

**可靠体验（10）**
- [ ] 空输入：`init` 退出码 0，写 `insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码：记录到 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺参数：`--input/--output/--work` 有缺省 `/input`、`/app/output`、`/app/work`。
- [ ] 节点规模爆炸：单文件 ≤2MB 跳过并记录；节点过多时渲染仍可完成。

**生态贡献（10）**
- [ ] `SKILL.md` 含用途、调用、输入输出、异常、限制、自检。
- [ ] 脚本纯标准库、路径相对、可直接复用。

## 三、降级与诚实原则

- 材料缺失 → `status=insufficient_evidence`，缺口写入 `uncertainty`/`pending_human`，**不编造**。
- 材料冲突 → 两条都记录并标 `source_conflict`。
- PDF 无法解析 / 软件缺失 → 写 `degradations`/`limitations`，只交付已能产出的部分，不虚构工具状态。
- 不生成示例答案、不预写正式测试产物、不编造评测分数；`runtime` 中平台未提供的数值留 `0`/`null`。
