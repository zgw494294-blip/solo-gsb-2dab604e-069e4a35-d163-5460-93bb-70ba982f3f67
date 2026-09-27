"""独立测量值乘积的一阶不确定度传播。

给定 1～8 个独立测量因子（值、标准不确定度、单位、整数幂），计算

    y    = ∏ x_i ^ p_i
    u(y) = √ Σ_i (∂y/∂x_i · u_i)²          （一阶合成标准不确定度）

偏导按乘积求导：∂y/∂x_i = p_i · x_i^(p_i-1) · ∏_{j≠i} x_j^p_j。

各因子先按单位比例换算到基本单位（m/kg/s/K），在基本单位下完成乘积与
传播，最后把 y 与 u(y) 换算到目标单位。中间量一律用 ``Fraction`` 精确
表示；u(y) 的平方根用整数开方按 ROUND_HALF_EVEN 舍入到 12 位小数。

约定与限制：

- 零值的零次幂按 1 处理（该因子对 y 与 u(y) 均无贡献）；
- 零值取负幂无定义，拒绝；
- ``°C`` / ``°F`` 是偏移温标，没有乘性比例语义，因子与目标单位一律拒绝；
- 乘积量纲 Σ p_i·dims_i 必须与目标单位一致；目标单位 ``"1"`` 表示无量纲。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import isqrt, prod

from .converter import (
    MAX_DECIMALS,
    InvalidValueError,
    OffsetScaleInCompoundError,
    Quantity,
    UnitSyntaxError,
    dim_description,
    parse_expression,
    parse_value,
)


# ---------------------------------------------------------------------------
# 输入结构与错误类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProductFactorInput:
    """一个测量因子：value/uncertainty 为有限十进制字符串，power ∈ [-6, 6]。"""
    value: str
    uncertainty: str
    unit: str
    power: int = 1


class PropagationError(Exception):
    """传播计算失败的基类；error 为对外的错误类型标识。"""

    error = "propagation_error"

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class FactorValueError(PropagationError):
    """value / uncertainty 不是有限十进制字符串。"""
    error = "invalid_value"


class NegativeUncertaintyError(PropagationError):
    """uncertainty 为负。"""
    error = "negative_uncertainty"


class ZeroNegativePowerError(PropagationError):
    """零值取了负幂。"""
    error = "zero_negative_power"


class ProductDimensionMismatchError(PropagationError):
    """乘积量纲与目标单位量纲不一致。"""
    error = "dimension_mismatch"


@dataclass(frozen=True)
class ProductUnitError(Exception):
    """单位表达式无法用于传播计算；factor 为因子下标，None 表示目标单位。"""
    message: str
    kind: str  # "syntax_error" | "offset_temperature_not_allowed"
    position: int | None
    factor: int | None = None


# ---------------------------------------------------------------------------
# 单位解析：复用表达式解析器，拒绝偏移温标；"1" 仅对目标单位表示无量纲
# ---------------------------------------------------------------------------

def _parse_unit(expr: str) -> Quantity:
    """解析单位表达式；°C / °F 无论单独使用还是参与复合一律拒绝。"""
    try:
        quantity = parse_expression(expr)
    except UnitSyntaxError as exc:
        raise ProductUnitError(exc.message, "syntax_error", exc.position)
    except OffsetScaleInCompoundError as exc:
        raise ProductUnitError(
            str(exc), "offset_temperature_not_allowed", exc.position)
    if quantity.offset_unit is not None:
        raise ProductUnitError(
            f"offset temperature scale {quantity.offset_unit!r} is not "
            "allowed; use K", "offset_temperature_not_allowed", quantity.pos)
    return quantity


def _parse_target(expr: str) -> Quantity:
    """解析目标单位；"1" 表示无量纲。"""
    if expr.strip() == "1":
        return Quantity((0, 0, 0, 0), Fraction(1))
    return _parse_unit(expr)


# ---------------------------------------------------------------------------
# 数值辅助
# ---------------------------------------------------------------------------

def _pow(x: Fraction, p: int) -> Fraction:
    """x^p；约定 0^0 = 1（零值负幂已在入口处拒绝）。"""
    if p == 0:
        return Fraction(1)
    return x ** p


def _sqrt_rounded(s: Fraction) -> Fraction:
    """√s 按 ROUND_HALF_EVEN 舍入到 MAX_DECIMALS 位小数（精确整数运算）。

    与 converter.format_result 的舍入语义一致：先求 q = ⌊√(s·10^24)⌋
    （= ⌊√(n·d)⌋ / d，n/d 为约分后的 s·10^24），再与 q + 1/2 比较
    （比较 4s·10^24 与 (2q+1)²），恰为一半时向偶数入。
    """
    if s <= 0:
        return Fraction(0)
    scaled = s * 10 ** (2 * MAX_DECIMALS)
    n, d = scaled.numerator, scaled.denominator
    q = isqrt(n * d) // d
    midpoint = (2 * q + 1) ** 2
    lhs = 4 * scaled
    if lhs > midpoint or (lhs == midpoint and q % 2 == 1):
        q += 1
    return Fraction(q, 10 ** MAX_DECIMALS)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def propagate_product(
        factors: list[ProductFactorInput],
        to: str) -> tuple[Fraction, Fraction]:
    """计算 y = ∏ x_i^power 及其一阶合成标准不确定度，并换算到目标单位。

    返回 (y, u)：y 为精确 Fraction；u 已按 ROUND_HALF_EVEN 舍入到
    12 位小数。输入非法时抛出 PropagationError 子类或 ProductUnitError。
    """
    values: list[Fraction] = []
    uncertainties: list[Fraction] = []
    quantities: list[Quantity] = []
    powers: list[int] = []

    for index, factor in enumerate(factors):
        try:
            value = parse_value(factor.value)
        except InvalidValueError as exc:
            raise FactorValueError(str(exc), factor=index, field="value")
        try:
            uncertainty = parse_value(factor.uncertainty)
        except InvalidValueError as exc:
            raise FactorValueError(str(exc), factor=index, field="uncertainty")
        if uncertainty < 0:
            raise NegativeUncertaintyError(
                f"uncertainty must be non-negative: {factor.uncertainty!r}",
                factor=index)
        try:
            quantity = _parse_unit(factor.unit)
        except ProductUnitError as exc:
            raise ProductUnitError(exc.message, exc.kind, exc.position, index)
        if value == 0 and factor.power < 0:
            raise ZeroNegativePowerError(
                "zero value cannot be raised to negative power "
                f"{factor.power}", factor=index)
        values.append(value)
        uncertainties.append(uncertainty)
        quantities.append(quantity)
        powers.append(factor.power)

    target = _parse_target(to)

    # 乘积量纲 = Σ p_i·dims_i，必须与目标单位一致
    product_dims = [0, 0, 0, 0]
    for quantity, power in zip(quantities, powers):
        for d in range(4):
            product_dims[d] += power * quantity.dims[d]
    if tuple(product_dims) != target.dims:
        raise ProductDimensionMismatchError(
            f"product dimension {dim_description(tuple(product_dims))} "
            f"cannot be converted to {dim_description(target.dims)}")

    # 换到基本单位（单位比例恒为正，不确定度直接乘比例）
    xs = [value * q.scale for value, q in zip(values, quantities)]
    us = [unc * q.scale for unc, q in zip(uncertainties, quantities)]

    xps = [_pow(x, p) for x, p in zip(xs, powers)]
    y = prod(xps, start=Fraction(1))

    # ∂y/∂x_i = p_i·x_i^(p_i-1)·∏_{j≠i} x_j^p_j；x_i≠0 时即 p_i·y/x_i
    n = len(xs)
    s = Fraction(0)
    for i in range(n):
        p = powers[i]
        if p == 0 or us[i] == 0:
            continue
        if xs[i] != 0:
            deriv = p * y / xs[i]
        elif p == 1:
            deriv = prod((xps[j] for j in range(n) if j != i),
                         start=Fraction(1))
        else:
            continue  # x_i = 0 且 p_i ≥ 2：偏导为 0
        s += (deriv * us[i]) ** 2

    y_target = y / target.scale
    u_target = _sqrt_rounded(s / target.scale ** 2)
    return y_target, u_target
