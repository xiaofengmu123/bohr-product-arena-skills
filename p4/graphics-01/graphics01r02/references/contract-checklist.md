# 契约与评分对照清单（contract-checklist.md）

## result.json 字段定义

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 从 case.json 提取，缺失填 `unknown` |
| `schema` | string | 固定 `editable_relationship_schematic/v1` |
| `status` | enum | `complete` / `partial` / `failed` |
| `editable_source_files` | array | 分层可编辑源文件，每项含 `path,kind,layers,editable_text,note` |
| `export_files` | array | 导出件，每项含 `path,format,width_px,height_px,dpi,color_space,font_check,note` |
| `provenance` | array | 图元溯源，每项 `element,source_ref,quote,mapping,version,confirmed` |
| `sources` | array | 材料引用，每项 `ref,path,locator,text_mode,used_for` |
| `uncertainty` | array | 不确定项 `item,reason,impact,mitigation` |
| `pending_human` | array | 需人工确认项 `item,reason` |
| `errors` / `degradations` | array | 错误与降级记录 |
| `change_log` | array | 修改记录 `time,action,detail` |
| `runtime` | object | `started_at,finished_at,wall_seconds,model_requests_used`（后者由平台记录，Skill 填 null） |

## 评分维度对照（逐条勾选）

### 实际效果（40 分）
- [ ] 任务完成度：从材料走通「材料→图稿→导出」，非模板插画
- [ ] 结果正确性：实体、关系、方向、层级、相对比例、数值单位与来源一致
- [ ] 结果完整性：覆盖实体/关系/图例/标注/单位/子图编号等必要图面元素
- [ ] 结果可核验性：`provenance` 每条有 `source_ref + quote`，`sources` 有 locator

### 使用增益（25 分）
- [ ] 质量增益：同输入下科学正确性、图面规范、可编辑性稳定提升
- [ ] 效率增益：减少人工修改量、返工轮次、重复绘图动作
- [ ] 增益可验证性：公平对照 + `change_log` 记录修改/返工，非文字自述

### 创新增益（15 分）
- [ ] 新增能力：文字→实体关系清单抽取、数据表驱动图例重绘、导出前合规检查
- [ ] 方法改进：实体抽取/关系建模/布局约束/图例映射/导出检查有实质改进
- [ ] 创新证据：相对基础 Agent 与现成工具的边界与价值被证明

### 可靠体验（10 分）
- [ ] 调用易用：Agent 正确触发，用户按说明可独立完成一次出图
- [ ] 运行稳定：不同图型、不同材料、重复运行持续产出可用图稿
- [ ] 异常处理：来源缺失/描述矛盾/数据图例不符/字体缺失/导出失败可识别处置

### 生态贡献（10 分）
- [ ] 文档完整：说明、依赖与字体、导出参数、示例、边界、维护信息齐全
- [ ] 资源可复用：模板、配色、图例规范、导出预设可直接复用
- [ ] 分享推广：沉淀可分享案例、图例规范与实践经验（不预写正式答案）

## 诚实性硬约束

- 不编造基因名/浓度/时间点/单位；不确定实体不画或标「推测」。
- 不伪造评测分数、运行结果、模型请求数。
- `status` 必须诚实：有任何未完成/未核实项则 `partial`，并在 `uncertainty`/`pending_human` 说明 limits。
- 空数组不得静默省略，须在对应字段说明原因（写入 `note` 或 `uncertainty`）。

## 自检命令

```bash
python scripts/main.py --input /input --output /app/output --work /app/work
python scripts/spec_to_svg.py --spec /app/work/figure_spec.json --out /app/output/figure01.svg
python scripts/selftest.py --result /app/output/result.json --output-dir /app/output
python scripts/selftest.py --selftest   # 验证渲染器与检查器自身可用
```

自检通过标准：`selftest.py --result` 退出码 0（无 errors；warnings 需逐一说明）。
