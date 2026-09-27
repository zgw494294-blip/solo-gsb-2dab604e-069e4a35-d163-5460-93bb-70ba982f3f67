"""不确定度传播核心逻辑测试（不经过 HTTP）。"""

from fractions import Fraction

import pytest

from app.converter import DimensionMismatchError, format_uncertainty
from app.propagation import (
    MeasurementInput,
    MeasurementInputError,
    UnitError,
    ZeroNegativePowerError,
    parse_target_unit,
    propagate_product,
)


def m(value, uncertainty, unit, power=1):
    return MeasurementInput(value=value, uncertainty=uncertainty,
                            unit=unit, power=power)


def target(expr):
    return parse_target_unit(expr)


# ---------------------------------------------------------------- 乘积与导数

def test_basic_product():
    # y = 2·3 = 6；u² = (3·0.1)² + (2·0.2)² = 0.09 + 0.16 = 0.25
    result = propagate_product([m("2", "0.1", "m"), m("3", "0.2", "m")],
                               target("m^2"))
    assert result.value == 6
    assert result.variance == Fraction(1, 4)


def test_values_scaled_to_base_units():
    result = propagate_product([m("2", "0.001", "km")], target("m"))
    assert result.value == 2000
    assert result.variance == 1


def test_result_scaled_to_target():
    result = propagate_product([m("2000", "1", "m")], target("km"))
    assert result.value == 2
    assert result.variance == Fraction(1, 1_000_000)


def test_power_and_derivative():
    # y = 2² = 4；∂y/∂x = 2·2 = 4 → u = 4·0.1 = 0.4
    result = propagate_product([m("2", "0.1", "m/s", power=2)],
                               target("(m/s)^2"))
    assert result.value == 4
    assert result.variance == Fraction(4, 25)


def test_negative_power():
    # y = 2⁻¹ = 0.5；∂y/∂x = −2⁻² = −1/4 → u = 0.025
    result = propagate_product([m("2", "0.1", "s", power=-1)], target("s^-1"))
    assert result.value == Fraction(1, 2)
    assert result.variance == Fraction(1, 1600)


def test_negative_value():
    # y = (−2)³ = −8；∂y/∂x = 3·(−2)² = 12 → u = 12·0.1 = 1.2
    result = propagate_product([m("-2", "0.1", "m", power=3)], target("m^3"))
    assert result.value == -8
    assert result.variance == Fraction(36, 25)


def test_zero_to_zero_power_is_one():
    # 0⁰ 按 1：y = 1·2 = 2；power 0 的因子不贡献不确定度
    result = propagate_product(
        [m("0", "0.5", "m", power=0), m("2", "0.1", "m")], target("m"))
    assert result.value == 2
    assert result.variance == Fraction(1, 100)


def test_zero_factor_positive_power():
    # y = 0·2 = 0；∂y/∂x_0 = 1·0⁰·2 = 2 → u = 2·0.5 = 1（另一导数含因子 0）
    result = propagate_product(
        [m("0", "0.5", "m"), m("2", "0.1", "m")], target("m^2"))
    assert result.value == 0
    assert result.variance == 1


def test_zero_uncertainty_contributes_nothing():
    result = propagate_product(
        [m("2", "0", "m"), m("3", "0.2", "m")], target("m^2"))
    assert result.value == 6
    assert result.variance == Fraction(4, 25)  # 只剩 (2·0.2)²


def test_dimensions_accumulate_with_powers():
    result = propagate_product(
        [m("1", "0.1", "m"), m("1", "0.1", "s^-2")], target("m/s^2"))
    assert result.value == 1


# ---------------------------------------------------------------- 422 情形

def test_zero_negative_power_rejected():
    with pytest.raises(ZeroNegativePowerError):
        propagate_product([m("0", "0.1", "m", power=-1)], target("m^-1"))


def test_negative_uncertainty_rejected():
    with pytest.raises(MeasurementInputError) as exc_info:
        propagate_product([m("1", "-0.1", "m")], target("m"))
    assert exc_info.value.kind == "invalid_uncertainty"
    assert exc_info.value.factor == 0


def test_invalid_value_and_uncertainty():
    for kwargs, kind in (({"value": "abc"}, "invalid_value"),
                         ({"uncertainty": "1e10000"}, "invalid_uncertainty")):
        args = {"value": "1", "uncertainty": "0.1", "unit": "m"}
        args.update(kwargs)
        with pytest.raises(MeasurementInputError) as exc_info:
            propagate_product([MeasurementInput(**args)], target("m"))
        assert exc_info.value.kind == kind


def test_offset_temperature_rejected():
    with pytest.raises(UnitError) as exc_info:
        propagate_product([m("1", "0.1", "°C")], target("K"))
    assert exc_info.value.kind == "offset_temperature_not_allowed"
    assert exc_info.value.factor == 0
    with pytest.raises(UnitError):
        propagate_product([m("1", "0.1", "K/°F")], target("1"))
    with pytest.raises(UnitError):
        parse_target_unit("°C")


def test_unit_syntax_error():
    with pytest.raises(UnitError) as exc_info:
        propagate_product([m("1", "0.1", "m s")], target("m"))
    assert exc_info.value.kind == "syntax_error"
    assert exc_info.value.position == 2


def test_dimension_mismatch():
    with pytest.raises(DimensionMismatchError):
        propagate_product([m("1", "0.1", "m")], target("s"))
    with pytest.raises(DimensionMismatchError):
        propagate_product([m("1", "0.1", "m")], target("1"))


def test_target_one_is_dimensionless():
    q = parse_target_unit("1")
    assert q.dims == (0, 0, 0, 0)
    assert q.scale == 1
    result = propagate_product([m("2", "0.1", "m/m")], q)
    assert result.value == 2
    assert result.variance == Fraction(1, 100)


# ---------------------------------------------------------------- format_uncertainty

@pytest.mark.parametrize("variance,expected", [
    (Fraction(0), "0"),
    (Fraction(4), "2"),
    (Fraction(1, 4), "0.5"),
    (Fraction(9, 100), "0.3"),
    (Fraction(2), "1.414213562373"),
    (Fraction(3), "1.732050807569"),
    (Fraction(1, 9), "0.333333333333"),
    # 恰为平局时向偶数进位：u = k.5 × 10⁻¹²
    (Fraction(25, 10 ** 26), "0"),               # 0.5 → 0（偶）
    (Fraction(225, 10 ** 26), "0.000000000002"),  # 1.5 → 2（偶）
    (Fraction(625, 10 ** 26), "0.000000000002"),  # 2.5 → 2（偶）
    (Fraction(1225, 10 ** 26), "0.000000000004"),  # 3.5 → 4（偶）
])
def test_format_uncertainty(variance, expected):
    assert format_uncertainty(variance) == expected


def test_format_uncertainty_rejects_negative():
    with pytest.raises(ValueError):
        format_uncertainty(Fraction(-1))
