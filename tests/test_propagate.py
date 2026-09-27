"""乘积不确定度传播的核心逻辑测试（不经过 HTTP）。"""

import pytest

from app.converter import format_result
from app.propagate import (
    FactorValueError,
    NegativeUncertaintyError,
    ProductDimensionMismatchError,
    ProductFactorInput,
    ProductUnitError,
    ZeroNegativePowerError,
    propagate_product,
)


def f(value, uncertainty, unit, power=1):
    return ProductFactorInput(
        value=value, uncertainty=uncertainty, unit=unit, power=power)


def run(factors, to):
    y, u = propagate_product(factors, to)
    return format_result(y), format_result(u)


# ---------------------------------------------------------------- 基本传播

def test_single_factor_passthrough():
    assert run([f("2", "0.01", "km")], "km") == ("2", "0.01")
    assert run([f("2", "0.01", "km")], "m") == ("2000", "10")


def test_rectangle_area():
    # y = 2·3 = 6；u = √((3·0.01)² + (2·0.02)²) = √0.0025 = 0.05
    assert run([f("2", "0.01", "m"), f("3", "0.02", "m")], "m^2") == ("6", "0.05")
    # 换算到 cm^2：值与不确定度同乘 10^4
    assert run([f("2", "0.01", "m"), f("3", "0.02", "m")],
               "cm^2") == ("60000", "500")


def test_power_factor():
    # y = 10³ = 1000 cm³；u = 3·10²·0.1 = 30 cm³
    assert run([f("10", "0.1", "cm", 3)], "cm^3") == ("1000", "30")
    assert run([f("10", "0.1", "cm", 3)], "m^3") == ("0.001", "0.00003")


def test_negative_power_and_irrational_uncertainty():
    # y = 2/4 = 0.5 m/s；两项贡献各 0.025，u = 0.025·√2
    assert run([f("2", "0.1", "m"), f("4", "0.2", "s", -1)], "m/s") == \
        ("0.5", "0.035355339059")


def test_compound_unit_scaling():
    # 36 km/h = 10 m/s；0.1 km/h = 1/36 m/s
    assert run([f("36", "0.1", "km/h")], "m/s") == ("10", "0.027777777778")
    # (2 m/s)² → (km/h)²：y = 4·(3.6)² = 51.84，u = 2·2·0.1·(3.6)² = 5.184
    assert run([f("2", "0.1", "m/s", 2)], "km^2/h^2") == ("51.84", "5.184")


def test_negative_value():
    # y = (-2)³ = -8；∂y/∂x = 3·(-2)² = 12，u = 12·0.1 = 1.2
    assert run([f("-2", "0.1", "m", 3)], "m^3") == ("-8", "1.2")


def test_dimensionless_target():
    # y = 2 m / 50 cm = 4；u = √((2·0.02)² + (8·0.005)²) = 0.04·√2
    assert run([f("2", "0.02", "m"), f("50", "0.5", "cm", -1)], "1") == \
        ("4", "0.056568542495")
    # 无量纲表达式 m/m 与 "1" 等价
    assert run([f("2", "0.02", "m"), f("50", "0.5", "cm", -1)], "m/m") == \
        ("4", "0.056568542495")


def test_value_rounding_half_even():
    # y = 1/3，无限循环按 ROUND_HALF_EVEN 保留 12 位小数
    assert run([f("1", "0", "m"), f("3", "0", "m", -1)], "1") == \
        ("0.333333333333", "0")


# ---------------------------------------------------------------- 零值约定

def test_zero_value_zero_power_counts_as_one():
    # 0^0 = 1：该因子对 y 与 u 均无贡献
    assert run([f("0", "5", "m", 0), f("2", "0.1", "s")], "s") == ("2", "0.1")
    assert run([f("0", "0.5", "m", 0)], "1") == ("1", "0")


def test_zero_value_power_one():
    # y = 0·3 = 0；∂y/∂x1 = 3，∂y/∂x2 = 0（y 为零）
    assert run([f("0", "0.1", "m"), f("3", "0.2", "m")], "m^2") == ("0", "0.3")


def test_zero_value_power_two_has_zero_derivative():
    assert run([f("0", "0.1", "m", 2)], "m^2") == ("0", "0")


def test_zero_value_negative_power_rejected():
    for power in (-1, -6):
        with pytest.raises(ZeroNegativePowerError) as exc_info:
            propagate_product([f("0", "0.1", "m", power)], "m")
        assert exc_info.value.extra["factor"] == 0


def test_zero_uncertainty_gives_zero_uncertainty():
    assert run([f("2", "0", "m"), f("3", "0", "m")], "m^2") == ("6", "0")


# ---------------------------------------------------------------- 平方根舍入

def test_sqrt_rounding_tie_to_even():
    # u 恰为 0.5·10^-12：前位 0 为偶 → 舍
    assert run([f("1", "0.0000000000005", "m")], "m") == ("1", "0")
    # u 恰为 1.5·10^-12：前位 1 为奇 → 入
    assert run([f("1", "0.0000000000015", "m")], "m") == \
        ("1", "0.000000000002")


def test_sqrt_of_perfect_square_is_exact():
    # u = 2·1·12 = 24，完全平方无舍入误差
    assert run([f("1", "12", "m", 2), f("0", "0", "m", 0)], "m^2") == \
        ("1", "24")


# ---------------------------------------------------------------- 422 类错误

def test_dimension_mismatch():
    with pytest.raises(ProductDimensionMismatchError):
        propagate_product([f("1", "0.1", "m")], "s")
    # 无量纲目标只接受无量纲乘积
    with pytest.raises(ProductDimensionMismatchError):
        propagate_product([f("1", "0.1", "m")], "1")


def test_negative_uncertainty_rejected():
    with pytest.raises(NegativeUncertaintyError) as exc_info:
        propagate_product([f("1", "0.1", "m"), f("2", "-0.5", "m")], "m^2")
    assert exc_info.value.extra["factor"] == 1


def test_invalid_value_and_uncertainty():
    with pytest.raises(FactorValueError) as exc_info:
        propagate_product([f("abc", "0.1", "m")], "m")
    assert exc_info.value.extra["field"] == "value"
    with pytest.raises(FactorValueError) as exc_info:
        propagate_product([f("1", "nan", "m")], "m")
    assert exc_info.value.extra["field"] == "uncertainty"


def test_offset_temperature_rejected():
    for unit in ("°C", "°F", "K*°C", "°F^2"):
        with pytest.raises(ProductUnitError) as exc_info:
            propagate_product([f("1", "0.1", unit)], "K")
        assert exc_info.value.kind == "offset_temperature_not_allowed"
        assert exc_info.value.factor == 0
    # 目标单位同样拒绝
    with pytest.raises(ProductUnitError) as exc_info:
        propagate_product([f("1", "0.1", "K")], "°C")
    assert exc_info.value.kind == "offset_temperature_not_allowed"
    assert exc_info.value.factor is None


def test_unit_syntax_error_carries_position():
    with pytest.raises(ProductUnitError) as exc_info:
        propagate_product([f("1", "0.1", "m s")], "m^2")
    assert exc_info.value.kind == "syntax_error"
    assert exc_info.value.position == 2
    # 因子单位不接受 "1"（"1" 仅是目标单位的无量纲记号）
    with pytest.raises(ProductUnitError):
        propagate_product([f("1", "0.1", "1")], "1")
