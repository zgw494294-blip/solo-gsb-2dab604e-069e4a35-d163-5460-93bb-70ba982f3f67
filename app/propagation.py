"""独立测量值乘积的一阶合成标准不确定度传播。

POST /propagate-product 的核心计算：

- 每个因子的 value、uncertainty 先按单位比例换算到基本单位（m/kg/s/K），
  量纲按（长度、质量、时间、温度）四维向量随幂次累计；
- y = ∏ x_i^power_i（零值的零次幂按 1）；
- u = √Σ (∂y/∂x_i · u_i)²，导数按乘积计算：
  ∂y/∂x_i = power_i · x_i^(power_i − 1) · ∏_{j≠i} x_j^power_j；
- y 与方差 u² 全部用 ``Fraction`` 精确累计，最后分别除以目标单位的比例
  及其平方换算到 ``to``；u 的开方与 ROUND_HALF_EVEN 舍入由
  :func:`app.converter.format_uncertainty` 用整数算术精确完成。

``to="1"`` 仅表示无量纲目标。``°C`` / ``°F`` 是偏移温标，没有唯一的乘性
比例，因子单位与目标单位一律拒绝；零值取负幂无意义，同样拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .converter import (
    DimensionMismatchError,
    InvalidValueError,
    OffsetScaleInCompoundError,
    Quantity,
    UnitSyntaxError,
    dim_description,
    parse_expression,
    parse_value,
)


# ---------------------------------------------------------------------------
# 错误类型
# ---------------------------------------------------------------------------

class MeasurementInputError(Exception):
    """因子的 value / uncertainty 非法（不是有限十进制字符串，或 uncertainty
    为负）；kind 为 "invalid_value" / "invalid_uncertainty"。"""

    def __init__(self, kind: str, message: str, factor: int):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.factor = factor


class ZeroNegativePowerError(Exception):
    """零值因子取了负幂；factor 为因子下标。"""

    def __init__(self, factor: int):
        super().__init__("zero value cannot be raised to a negative power")
        self.factor = factor


@dataclass(frozen=True)
class UnitError(Exception):
    """单位表达式无法用于不确定度传播。

    kind 为 "syntax_error" / "offset_temperature_not_allowed"；factor 为
    因子下标，None 表示目标单位 to。
    """
    message: str
    kind: str
    position: int | None
    factor: int | None = None


# ---------------------------------------------------------------------------
# 输入与结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MeasurementInput:
    """一个独立测量值因子：value/uncertainty 为原始字符串，power ∈ [-6, 6]。"""
    value: str
    uncertainty: str
    unit: str
    power: int = 1


@dataclass(frozen=True)
class ProductResult:
    """换算到目标单位后的乘积 y 与合成方差 u²（均为精确有理数）。"""
    value: Fraction
    variance: Fraction


@dataclass(frozen=True)
class _Factor:
    """换算到基本单位后的因子。"""
    value: Fraction
    uncertainty: Fraction
    power: int


# ---------------------------------------------------------------------------
# 单位解析：复用表达式解析器，拒绝 °C / °F
# ---------------------------------------------------------------------------

def _parse_ratio_unit(expr: str, factor: int | None) -> Quantity:
    """解析单位表达式；偏移温标没有唯一的乘性比例，一律拒绝。"""
    try:
        quantity = parse_expression(expr)
    except UnitSyntaxError as exc:
        raise UnitError(exc.message, "syntax_error", exc.position, factor)
    except OffsetScaleInCompoundError as exc:
        raise UnitError(str(exc), "offset_temperature_not_allowed",
                        exc.position, factor)
    if quantity.offset_unit is not None:
        raise UnitError(
            f"offset temperature scale {quantity.offset_unit!r} is not "
            "allowed; use K", "offset_temperature_not_allowed",
            quantity.pos, factor)
    return quantity


def parse_target_unit(expr: str) -> Quantity:
    """解析目标单位；to="1" 仅表示无量纲（比例 1）。"""
    if expr.strip() == "1":
        return Quantity((0, 0, 0, 0), Fraction(1))
    return _parse_ratio_unit(expr, None)


# ---------------------------------------------------------------------------
# 核心计算
# ---------------------------------------------------------------------------

def _prepare(measurement: MeasurementInput,
             index: int) -> tuple[_Factor, tuple[int, int, int, int]]:
    """解析并校验单个因子，换算到基本单位。"""
    try:
        value = parse_value(measurement.value)
    except InvalidValueError as exc:
        raise MeasurementInputError("invalid_value", str(exc), index)
    try:
        uncertainty = parse_value(measurement.uncertainty)
    except InvalidValueError as exc:
        raise MeasurementInputError("invalid_uncertainty", str(exc), index)
    if uncertainty < 0:
        raise MeasurementInputError(
            "invalid_uncertainty",
            f"uncertainty must be non-negative: {measurement.uncertainty!r}",
            index)
    if value == 0 and measurement.power < 0:
        raise ZeroNegativePowerError(index)
    quantity = _parse_ratio_unit(measurement.unit, index)
    return _Factor(value * quantity.scale,
                   uncertainty * quantity.scale,
                   measurement.power), quantity.dims


def propagate_product(measurements: list[MeasurementInput],
                      target: Quantity) -> ProductResult:
    """计算 y = ∏ x_i^power_i 及其一阶合成方差，并换算到目标单位。

    失败抛出 MeasurementInputError / ZeroNegativePowerError / UnitError /
    DimensionMismatchError。
    """
    factors: list[_Factor] = []
    dims = (0, 0, 0, 0)
    for index, measurement in enumerate(measurements):
        factor, factor_dims = _prepare(measurement, index)
        factors.append(factor)
        dims = tuple(a + factor.power * b for a, b in zip(dims, factor_dims))

    if dims != target.dims:
        raise DimensionMismatchError(
            f"dimension mismatch: product has {dim_description(dims)} "
            f"but target unit is {dim_description(target.dims)}")

    # 各因子的 x_i^power_i；Fraction(0) ** 0 == 1，零值的零次幂按 1。
    terms = [factor.value ** factor.power for factor in factors]
    y = Fraction(1)
    for term in terms:
        y *= term

    variance = Fraction(0)
    for i, factor in enumerate(factors):
        if factor.power == 0 or factor.uncertainty == 0:
            continue
        # ∂y/∂x_i = power · x_i^(power − 1) · ∏_{j≠i} x_j^power_j
        derivative = factor.power * (factor.value ** (factor.power - 1))
        for j, term in enumerate(terms):
            if j != i:
                derivative *= term
        contribution = derivative * factor.uncertainty
        variance += contribution ** 2

    return ProductResult(y / target.scale,
                         variance / target.scale ** 2)
