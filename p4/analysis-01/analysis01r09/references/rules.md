# 清洗规则与证据规范（sci-table-clean 参考）

本文是 `SKILL.md` 第 5 节的展开：Agent 在把字段级结论写成 `/app/work/rules.json` 时按此执行；脚本只做确定性动作，不做智能裁决。

## 1. 缺失值四分类（不互覆盖、不插补）

| 类别 | 含义 | 典型原文 | 处理 |
|---|---|---|---|
| `not_detected` | 未检测到信号，非缺失非 0 | ND, n.d., not detected, 未检测, <DL | 保留原文并打标，不转 0，不插补 |
| `below_lod` | 低于检出限，左删失 | <0.01, below LOD, 低于检出限, <检测限 | 保留原文与不等式，不当作 0 或缺失 |
| `recorded_missing` | 录入缺失/哨兵 | 空, NA, -999, null, 缺失 | 保留原文并分类，默认不插补 |
| `true_zero` | 真值 0 | 0, 0.0 | 保留，不并入缺失 |

规则：四类**保留原文并分类**，互不覆盖；默认不插补。确需标记/替换时在 `change_log` 记录 `step/action` 与 locator + 逐字 quote。

## 2. 异常值

- 判据以数据字典 + 单位/QC 规则给出的**物理上下界**为准；无上下界时标 `uncertainty`，不用"均值 ± n 倍标准差"武断删真值。
- 动作三选一：`keep`（真实高值，保留并说明）、`flag`（打标保留，默认）、`winsorize`/`drop`（仅当 QC 规则明确允许，写理由与 locator）。
- 每个异常结论写 `field / value / bounds / action / locator / quote`。

## 3. 重复记录

- 主键取真实标识：样品编号、时间戳、受试者标识，**不默认整行相等**。
- 近似重复先归一化：大小写、全半角（NFKC）、首尾空格、连续空白合并、单位写法差异（如 `mg/L` vs `mg/l` 按规则映射）。
- 明确保留哪一条及依据（`duplicate_keep`: `first`/`last`/`most_complete`）；冲突并列并标 `source_conflict`。

## 4. 格式与单位转换

- 编码先于解析（`utf-8-sig → gb18030 → latin-1` 容错，BOM/乱码记 `degradations`）。
- 科学计数法、千分位、有效数字**不做静默截断**；转换用全精度 float，原文保留在结果表。
- 单位换算必须留映射表：`from_unit / to_unit / factor / formula`；换算后值写入 `<field>__converted`，原列不变。
- 需摩尔质量的单位（如 mol/L ↔ g/L）**不猜摩尔质量**，标 `pending_human`。

## 5. 质量报告

- 字段级清单：缺失四分类计数、异常条数、重复组、转换记录、处理动作与影响范围。
- **报告数字必须由 `cleaned_data.csv` 实时重算**：`data_profile.json` 与 `quality_report.md` 由同一内存对象生成，`--phase selfcheck` 再读 CSV 重算核验，禁止手写数字。

## 6. 证据溯源

- 每个关键结论给 `locator + quote`：`locator` 形如 `材料路径#段落/行列/字段`，`quote` 为**逐字**原文片段。
- 写入 `evidence_map.json`（`claims[]`，每项 `claim/locator/quote/note`）与 `result.json` 的 `sources[]`。
- 冲突信息两项并列，标 `source_conflict`；缺失写 `pending_human` 或 `not_reported`，不虚构引用/DOI/数值。

## 7. rules.json 字段级规则（外置配置）

```json
{
  "primary_keys": ["sample_id"],
  "duplicate_keep": "first",
  "fields": {
    "conc_mgL": {
      "unit": "mg/L",
      "convert_to": "ug/mL",
      "convert_factor": 1.0,
      "physical_min": 0,
      "physical_max": 100,
      "outlier_action": "flag",
      "not_detected_tokens": ["ND"],
      "below_lod_tokens": ["<0.01", "below LOD"],
      "missing_sentinels": ["", "-999"],
      "true_zero_tokens": ["0"]
    }
  }
}
```

`convert_factor`/`convert_to` 为换算比与目标单位；不给则不做数值换算。物理上下界缺省时不判异常（诚实标注 `uncertainty`），不臆造范围。

## 8. 降级与异常处置表

| 场景 | 处置 | status |
|---|---|---|
| 空输入目录 / 无 case.json | 写合法 result.json，列缺口 | `insufficient_evidence` |
| 坏 JSON / 坏编码 | 记录到 `errors`/`degradations`，继续处理其余材料 | 最多 `partial` |
| 无主表 / 表损坏 | 保留已成功产物，列缺口 | `insufficient_evidence` 或 `partial` |
| 单位不明 / 规则冲突 / 主键冲突 | 标 `uncertainty`/`pending_human`，不静默处理 | `partial` |
| 缺软件（渲染/OCR） | `degradations`/`errors` 写失败步骤，保留合法产物 | 最多 `partial` |
| 写盘失败 | 降级写最小合法 result.json，进程不崩溃 | `input_error` |

同一输入重复运行，数据结果必须一致（无随机、无网络；时间戳仅作元数据）。
