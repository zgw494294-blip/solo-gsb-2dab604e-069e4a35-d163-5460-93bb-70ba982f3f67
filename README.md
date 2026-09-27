# 单位换算 API（Unit Converter）

基于 **Python + FastAPI + Decimal/Fraction** 的工程单位换算服务。
支持长度、质量、时间、温度四维量纲核对，复合单位表达式，°C / °F 偏移温标换算，
由多条乘积等式推断未知变量的四维量纲，从具名量中搜索最简无量纲乘积，
以及独立测量值乘积的一阶不确定度传播。

## 启动（单条命令）

```bash
docker compose up --build
```

启动后本机访问：

- API 地址：**http://localhost:8000**
- 交互式文档（Swagger UI）：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

## API

### POST /convert

请求体（JSON，`value` 为有限十进制字符串）：

```json
{
  "value": "36",
  "from": "km/h",
  "to": "m/s"
}
```

响应（`result` 为字符串）：

```json
{"result": "10"}
```

示例：

```bash
curl -X POST http://localhost:8000/convert \
  -H 'Content-Type: application/json' \
  -d '{"value": "100", "from": "°C", "to": "°F"}'
# {"result":"212"}
```

### POST /infer-dimensions

依据多条乘积等式推断未知变量的（长度、质量、时间、温度）四维量纲。
请求体含 `equations` 数组；每条等式有非空因子数组 `left`、`right`，
因子恰含 `var`（未知变量名）或 `unit`（单位表达式）一个字段，
可带 **-6～6 的整数 `power`**（默认 1）；所有等式中至少出现一个 `var`。

把各因子的幂次合并后，每条等式在四个维度上各给出一个线性约束，跨等式组成
有理系数线性方程组。仅当每个变量的四个指数都有**唯一整数解**时返回结果，
`dimensions` 按变量名排序：

```json
{
  "equations": [
    {"left": [{"var": "F"}], "right": [{"var": "m"}, {"var": "a"}]},
    {"left": [{"var": "a"}], "right": [{"unit": "m/s^2"}]},
    {"left": [{"var": "m"}], "right": [{"unit": "kg"}]}
  ]
}
```

响应：

```json
{
  "dimensions": {
    "F": {"length": 1, "mass": 1, "time": -2, "temperature": 0},
    "a": {"length": 1, "mass": 0, "time": -2, "temperature": 0},
    "m": {"length": 0, "mass": 1, "time": 0, "temperature": 0}
  }
}
```

示例：

```bash
curl -X POST http://localhost:8000/infer-dimensions \
  -H 'Content-Type: application/json' \
  -d '{"equations":[{"left":[{"var":"v"}],"right":[{"unit":"m/s"}]}]}'
# {"dimensions":{"v":{"length":1,"mass":0,"time":-1,"temperature":0}}}
```

单位因子复用单位表达式解析器但**只取量纲**，因此不接受 `°C` / `°F`
（偏移温标没有唯一的乘性量纲），请改用 `K`。约束矛盾、欠定或唯一解含
分数指数时均返回 422，并以不同的 `error` 区分（见下表）。

### POST /dimensionless-group

从 **1～8 个**具名量的单位表达式中搜索一个**最简无量纲乘积**（如雷诺数一类
的无量纲群）。请求体 `quantities` 为 `{name, unit}` 数组：`name` 非空白且互异；
`unit` 复用单位表达式解析器但**只取四维量纲**，不接受 `°C` / `°F`：

```json
{
  "quantities": [
    {"name": "rho", "unit": "kg/m^3"},
    {"name": "v",   "unit": "m/s"},
    {"name": "L",   "unit": "m"},
    {"name": "mu",  "unit": "kg/(m*s)"}
  ]
}
```

响应 `exponents` 含所有名称（**按名称排序**，含零指数），下例表示
`L^1 · mu^-1 · rho^1 · v^1` 为无量纲乘积：

```json
{"exponents": {"L": 1, "mu": -1, "rho": 1, "v": 1}}
```

求解规则：整数指数每项 ∈ **-6～6** 且不全为零，四个维度的加权和均为零；
只保留非零指数**最大公约数为 1**、（按名称排序后）**首个非零指数为正**的解；
可行解中先取**指数绝对值之和最小**者，再按排序后的指数元组**字典序**取唯一
结果。范围内无解返回 422（`no_dimensionless_group`）。

```bash
curl -X POST http://localhost:8000/dimensionless-group \
  -H 'Content-Type: application/json' \
  -d '{"quantities":[{"name":"rho","unit":"kg/m^3"},{"name":"v","unit":"m/s"},{"name":"L","unit":"m"},{"name":"mu","unit":"kg/(m*s)"}]}'
# {"exponents":{"L":1,"mu":-1,"rho":1,"v":1}}
```

### POST /propagate-product

对 **1～8 个**独立测量因子计算乘积 `y = ∏ x_i^power` 及其**一阶合成标准
不确定度** `u = √Σ (∂y/∂x_i · u_i)²`。每个因子含 `value`、`uncertainty`
（均为有限十进制字符串，`uncertainty` 非负）、`unit`（单位表达式）与可选
`power`（**-6～6 整数**，默认 1）；`to` 为目标单位表达式，**`"1"` 表示
无量纲**：

```json
{
  "factors": [
    {"value": "2", "uncertainty": "0.01", "unit": "m"},
    {"value": "3", "uncertainty": "0.02", "unit": "m"}
  ],
  "to": "m^2"
}
```

响应（`value`、`uncertainty` 均为字符串）：

```json
{"value": "6", "uncertainty": "0.05"}
```

计算规则：各因子先按单位比例换算到基本单位（m/kg/s/K），在基本单位下求
`y = ∏ x_i^power` 与 `u = √Σ (∂y/∂x_i·u_i)²`（偏导按乘积求导，
`∂y/∂x_i = p_i·x_i^(p_i-1)·∏_{j≠i} x_j^p_j`），再把 `y`、`u` 换算到 `to`；
结果按 **ROUND_HALF_EVEN** 保留最多 12 位小数。约定**零值的零次幂为 1**
（该因子对结果无贡献）；**零值负幂**、**°C / °F**（单独或复合）、乘积量纲
与 `to` 不符均返回 422。

```bash
curl -X POST http://localhost:8000/propagate-product \
  -H 'Content-Type: application/json' \
  -d '{"factors":[{"value":"2","uncertainty":"0.01","unit":"m"},{"value":"3","uncertainty":"0.02","unit":"m"}],"to":"m^2"}'
# {"value":"6","uncertainty":"0.05"}
```

## 单位与表达式语法

- 支持单位：`m`、`km`、`cm`、`mm`（长度）；`kg`、`g`（质量）；`s`、`min`、`h`（时间）；`K`、`°C`、`°F`（温度）。
- 表达式允许：括号 `()`、乘法 `*`、除法 `/`，以及 `^` 后的 **-6～6 整数幂**（如 `m^2`、`s^-2`）。
- 忽略空白；**不允许隐式乘法**（`m s`、`2m`、`m(kg)` 均为语法错误）。
- 量纲按（长度、质量、时间、温度）四维核对，`from` 与 `to` 量纲必须一致；比例用有理数精确累计。
- `°C`、`°F` 为偏移温标，**只允许单独使用**（可套括号，如 `(°C)`），不得参与 `*`、`/`、`^` 复合运算；换算先经 K（`°C + 273.15 = K`，`(°F + 459.67) × 5/9 = K`）。复合式中的 `K` 只按比例处理。
- 结果按 **ROUND_HALF_EVEN** 保留最多 **12 位小数**，并去掉末尾零（如 `2.50` → `2.5`，`1/3` → `0.333333333333`）。

## 错误响应（均为 422）

语法错误返回从 **0** 开始的字符位置：

```json
{
  "detail": {
    "error": "syntax_error",
    "message": "unexpected 's' (missing operator; implicit multiplication is not allowed)",
    "field": "from",
    "position": 2
  }
}
```

| `error` | 含义 |
| --- | --- |
| `syntax_error` | 单位表达式语法错误，含 `field`（`from`/`to`）与 0 起始的 `position` |
| `dimension_mismatch` | 两侧量纲不一致 |
| `offset_temperature_in_compound` | °C / °F 参与了复合运算 |
| `invalid_value` | `value` 不是有限十进制字符串 |
| `no_variable` | `/infer-dimensions` 的所有等式中没有任何 `var` 因子 |
| `inconsistent_constraints` | 约束互相矛盾（如同一变量同时被约束为 `m` 与 `s`），含首个矛盾维度 `dimension` |
| `underdetermined` | 约束不足，存在无法唯一确定的变量，含 `free_variables` 列表 |
| `fractional_dimensions` | 解唯一但含分数指数（如 `x^2 = m`），含相关 `variables` 列表 |
| `offset_temperature_not_allowed` | `/infer-dimensions` 的单位因子使用了 °C / °F；含 `equation`、`side`、`factor` 坐标（及单位表达式内 `position`） |
| `no_dimensionless_group` | `/dimensionless-group` 在 -6～6 指数范围内不存在非平凡无量纲组合 |
| `negative_uncertainty` | `/propagate-product` 的 `uncertainty` 为负，含 `factor` 下标 |
| `zero_negative_power` | `/propagate-product` 的零值因子取了负幂，含 `factor` 下标 |

`/infer-dimensions` 的单位因子语法错误同样返回 `syntax_error`，并带
`equation`（等式下标）、`side`（`left`/`right`）、`factor`（因子下标）
与 0 起始的 `position`。

`/dimensionless-group` 的单位错误同样返回 `syntax_error` /
`offset_temperature_not_allowed`，坐标字段为 `quantity`（量在
`quantities` 中的下标）与 0 起始的 `position`；`name` 空白或重复、
`quantities` 数量不在 1～8 等请求体问题由 FastAPI 校验返回 422。

`/propagate-product` 的 `invalid_value` / `syntax_error` /
`offset_temperature_not_allowed` 带 `factor`（因子下标，数值错误另带
`field`：`value` / `uncertainty`）与 0 起始的 `position`；目标单位 `to`
的解析错误带 `field: "to"`；乘积量纲与 `to` 不符返回 `dimension_mismatch`；
`factors` 数量不在 1～8、`power` 越界等请求体问题由 FastAPI 校验返回 422。

请求体缺字段、类型错误等由 FastAPI 校验，同样返回 422。

## 本地开发（不用 Docker）

```bash
pip install -r requirements-dev.txt
python -m pytest            # 运行测试
uvicorn app.main:app --reload --port 8000
```

## 项目结构

```
app/
  converter.py      # 词法/语法分析、量纲核对、有理数换算、ROUND_HALF_EVEN 格式化
  dimensions.py     # 跨等式线性约束（有理 Gauss-Jordan 消元）与四维量纲推断
  dimensionless.py  # 最简无量纲乘积搜索（中途相遇枚举 ±6 整数指数）
  propagate.py      # 独立测量值乘积的一阶不确定度传播（有理数 + 精确整数开方舍入）
  main.py           # FastAPI 应用（POST /convert、/infer-dimensions、/dimensionless-group、/propagate-product、GET /health）
tests/              # pytest 测试（核心逻辑 + HTTP API）
Dockerfile
docker-compose.yml
requirements.txt / requirements-dev.txt
```
