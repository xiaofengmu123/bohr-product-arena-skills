# 自检清单、降级与合同回填细则

交付前逐条勾选。清单与评分维度一一对应，任何一条不满足都要回到对应步骤修正，而不是在 result.json 里自我声明通过。

## 1. 交付前自检（对应评分维度）

### 实际效果（40 分）
- [ ] `manuscript.md` 四段齐全且承接：研究背景、相关工作、研究空白、贡献，可直接进入论文。
- [ ] 背景指向具体未解决问题，无“近年来受到广泛关注”式空转。
- [ ] 相关工作按问题 / 方法 / 技术路线分组，含问题设定、方法、数据与评价维度，组内同维度比较。
- [ ] 他人工作转述与原文一致，引用元数据真实，适用条件与局限保留。
- [ ] 空白由已引文献缺口推出，并写明本文填补方式；作者判断已标注。
- [ ] 每条贡献可映射到正文方法 / 图表 / 结果，不夸大适用范围。
- [ ] 引用覆盖关键工作、代表工作与近期进展；每条引用落在具体论断上。

### 可靠体验（10 分）
- [ ] 空输入目录：脚本退出码 0，写出 `insufficient_evidence`，不抛异常。
- [ ] 坏 JSON / 坏编码：记录到 `errors` / `degradations`，其余材料继续处理。
- [ ] 缺参数：脚本缺省指向 `/input`、`/app/output`、`/app/work`。
- [ ] 重复运行：两次产物字节一致（确定性；`runtime` 无墙钟字段）。

### 产物格式
- [ ] `result.json` 可被 `json.load` 解析。
- [ ] `manuscript.md` 非空且无占位空话；`evidence_map.json` 每个 `[S#]` 有 locator + 逐字 quote。
- [ ] 无编造数据、引用、DOI 或评测分数。

## 2. result.json 合同回填细则

- 合同字段以 `case.json` 为准，按优先级发现：`output_contract` → `outputContract` → `output` → `expected_output` → `expectedOutput` → `fields` → `required_fields` → `requiredFields`。
- 字段名为字符串则作为 result.json 顶层键，缺省 `null`；为对象则按子键结构回填 `{}`；为列表则回填 `[]`；类型说明 `array`/`object` 分别映射 `[]`/`{}`。
- 合同字段名与标准字段名冲突时，**合同字段名优先**，标准字段若重名则不覆盖。
- `scripts/main.py scan` 的 `contract_fields` 为最佳努力抽取；Agent 必须读 `work/case_normalized.json` 核实，缺失的合同字段手工补进 result.json。
- 标准字段固定为：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`artifacts`、`runtime`，外加 `case_id`。
- `status` 判定：四段齐全 + 证据齐全 + 合同字段全部回填 → `ok`；有可用稿但存缺口 → `partial`；材料不足以成稿 → `insufficient_evidence`；输入无法解析 → `input_error`。

## 3. 降级处置表

| 场景 | 处置 |
|---|---|
| 材料不足 / 文献库为空 | `status=insufficient_evidence`；正文只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充 |
| 引用查不到原文 | `pending_human` + `uncertainty`，不臆造元数据 |
| 目标期刊格式未知 | 作者—年份 + `uncertainty` 声明假设，不冒充特定格式 |
| 语言切换 | 术语 / 数字 / 引用原位，仅换语言，限定强度不变 |
| PDF / 软件不可用 | `degradations` 记录，用现有文本继续，不伪造缺失状态 |
| 写盘失败 | 最小合法 result.json（`status=input_error` + `errors`）收尾，不让进程崩溃 |

## 4. 脚本调用（相对路径）

```bash
python3 -B scripts/main.py scan --input /input --work /app/work
python3 -B scripts/main.py skeleton --work /app/work --output /app/output
# Agent 在两步之间完成 facts.md / manuscript.md / evidence_map.json / change_log.json 起草
python3 -B scripts/main.py validate --work /app/work --output /app/output
python3 -B scripts/main.py finalize --work /app/work --output /app/output   # 确定性收尾
python3 -B scripts/main.py selftest                                        # 提交前自检
python3 -B scripts/main.py run --input /input --work /app/work --output /app/output
```

脚本缺省 `--input /input --output /app/output --work /app/work`；本地演示可传绝对路径。脚本只做发现 / 骨架 / 校验 / 确定性收尾，正文写作由 Agent 完成。

## 5. 维护信息

- 适用：中英文科研论文引言与相关工作的起草、改写、补引用、核对。
- 支持引用格式：案例指定格式优先；缺省作者—年份，可改数字式（改动记 `change_log`）。
- 限制：不做期刊排版模板适配；不生成材料外事实；不自动联网补参考文献。
- 依赖：仅 Python 3.9+ 标准库，无第三方包。
