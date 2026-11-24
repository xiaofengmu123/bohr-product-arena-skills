# 公共数据集字段语义先验（待核对，非权威）

本文件记录三个公开 UCI 数据集的**已知字段语义先验**，用途是加速阶段 1 的字段解释。
**硬规则：本先验必须与实际 `/input` 数据字典、`PRAGMA table_info` 结果逐一核对；列名、顺序、单位、分隔符以实际快照为准，冲突时以数据字典为准。** 本文件不含任何计算结果或预写答案。

## 1. Concrete Compressive Strength（UCI，约 1030 条，无表头）

原始数据为空格/制表符分隔、通常无表头，8 个输入特征 + 1 个目标：

| 列（常见顺序） | 含义 | 单位 |
|-|-|-|
| 1 | Cement（水泥） | kg/m³ |
| 2 | Blast Furnace Slag（高炉矿渣） | kg/m³ |
| 3 | Fly Ash（粉煤灰） | kg/m³ |
| 4 | Water（水） | kg/m³ |
| 5 | Superplasticizer（减水剂） | kg/m³ |
| 6 | Coarse Aggregate（粗骨料） | kg/m³ |
| 7 | Fine Aggregate（细骨料） | kg/m³ |
| 8 | Age（龄期） | day |
| 9 | Concrete compressive strength（抗压强度，目标） | MPa |

易混点：Superplasticizer 存在 0 值；Age 是离散正整数；目标列是连续数值，不是类别。

## 2. Iris（UCI，150 条，Bezdek 来源变体）

4 个连续特征 + 1 个类别（3 类各 50 条）：

| 列 | 含义 | 单位 |
|-|-|-|
| sepal length | 萼片长度 | cm |
| sepal width | 萼片宽度 | cm |
| petal length | 花瓣长度 | cm |
| petal width | 花瓣宽度 | cm |
| class | 品种 | Iris-setosa / Iris-versicolor / Iris-virginica |

易混点：4 个长度特征全为数值，不要与类别列混淆；类别是文本，不参与均值/相关。

## 3. Wine（UCI，178 条）

13 个连续特征 + 1 个整数类别（1/2/3，对应 3 个产地）：

| 列 | 含义 |
|-|-|
| Alcohol | 酒精度 |
| Malic acid | 苹果酸 |
| Ash | 灰分 |
| Alcalinity of ash | 灰分碱度 |
| Magnesium | 镁 |
| Total phenols | 总酚 |
| Flavanoids | 类黄酮 |
| Nonflavanoid phenols | 非类黄酮酚 |
| Proanthocyanins | 原花青素 |
| Color intensity | 颜色强度 |
| Hue | 色调 |
| OD280/OD315 of diluted wines | 稀释酒 OD280/OD315 |
| Proline | 脯氨酸 |
| class | 产地类别（1/2/3） |

易混点：class 是**整数编码类别**，若被声明为文本且值全为数字，会命中 `coded_as_text`，转数值前必须确认其语义是类别而非连续量。
