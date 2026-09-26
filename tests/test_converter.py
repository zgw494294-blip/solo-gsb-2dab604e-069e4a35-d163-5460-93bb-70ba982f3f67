"""核心换算逻辑测试（不经过 HTTP）。"""

import pytest

from app.converter import (
    DimensionMismatchError,
    InvalidValueError,
    OffsetScaleInCompoundError,
    UnitSyntaxError,
    convert,
    format_result,
    parse_value,
)


# ---------------------------------------------------------------- 基本换算

@pytest.mark.parametrize("value,frm,to,expected", [
    # 长度
    ("1", "km", "m", "1000"),
    ("1", "m", "km", "0.001"),
    ("2.5", "cm", "mm", "25"),
    ("1", "mm", "m", "0.001"),
    ("1", "km", "cm", "100000"),
    # 质量
    ("1", "kg", "g", "1000"),
    ("500", "g", "kg", "0.5"),
    # 时间
    ("1", "h", "min", "60"),
    ("1", "min", "s", "60"),
    ("1", "h", "s", "3600"),
    ("1", "s", "h", "0.000277777778"),
    # 复合表达式
    ("1", "m/s", "km/h", "3.6"),
    ("36", "km/h", "m/s", "10"),
    ("1", "kg*m/s^2", "kg*m/s^2", "1"),
    ("1", "kg*m/s^2", "g*km/h^2", "12960000"),
    ("1", "(m/s)^2", "(km/h)^2", "12.96"),
    ("1", "m^2", "cm^2", "10000"),
    ("1", "m^-1", "cm^-1", "0.01"),
    ("1", "m^3", "mm^3", "1000000000"),
    ("1", "s^-1", "h^-1", "3600"),
    # 括号与空白
    ("1", " kg * m / s^2 ", "kg*m/s^2", "1"),
    ("1", "m ^ 2", "cm^2", "10000"),
    ("1", "((km))", "(m)", "1000"),
    # 无量纲
    ("1", "m/m", "km/km", "1"),
    ("1", "m/m", "km/m", "0.001"),
    ("2", "m/km", "km/m", "0.000002"),
])
def test_convert_ok(value, frm, to, expected):
    assert convert(value, frm, to) == expected


def test_dimensionless_empty_not_allowed():
    # 空表达式是语法错误，无量纲需写成 m/m 之类
    with pytest.raises(UnitSyntaxError):
        convert("1", "m/km", "")


# ---------------------------------------------------------------- 温度

@pytest.mark.parametrize("value,frm,to,expected", [
    ("0", "°C", "K", "273.15"),
    ("273.15", "K", "°C", "0"),
    ("100", "°C", "°F", "212"),
    ("32", "°F", "°C", "0"),
    ("212", "°F", "°C", "100"),
    ("32", "°F", "K", "273.15"),
    ("0", "K", "°F", "-459.67"),
    ("-40", "°C", "°F", "-40"),
    ("-40", "°F", "°C", "-40"),
    ("37.5", "°C", "°F", "99.5"),
    ("1", "K", "K", "1"),
    ("25", "°C", "°C", "25"),
    ("1", "(°C)", "K", "274.15"),   # 括号不改变“单独使用”的性质
    ("1", "K/(kg*m^2/s^2)", "K*s^2/(kg*m^2)", "1"),  # 复合式中的 K 只按比例
])
def test_temperature(value, frm, to, expected):
    assert convert(value, frm, to) == expected


@pytest.mark.parametrize("expr", [
    "°C*m", "m*°F", "°C/s", "°C^2", "°C^-1", "°F/°C", "K*°C", "(°C)*m",
])
def test_offset_scale_in_compound(expr):
    with pytest.raises(OffsetScaleInCompoundError):
        convert("1", expr, "K")
    with pytest.raises(OffsetScaleInCompoundError):
        convert("1", "K", expr)


# ---------------------------------------------------------------- 量纲核对

@pytest.mark.parametrize("frm,to", [
    ("m", "s"), ("kg", "m"), ("m/s", "m"), ("K", "m"), ("°C", "kg"),
    ("m^2", "m"), ("kg*m/s^2", "kg*m/s"), ("h", "m/s"),
])
def test_dimension_mismatch(frm, to):
    with pytest.raises(DimensionMismatchError):
        convert("1", frm, to)


# ---------------------------------------------------------------- 语法错误（位置从 0 开始）

@pytest.mark.parametrize("expr,pos", [
    ("", 0),
    ("*m", 0),
    ("2m", 0),
    ("m*", 2),
    ("m/", 2),
    ("m s", 2),
    ("ms", 1),
    ("m(m)", 1),
    ("(m", 2),
    ("m)", 1),
    ("()", 1),
    ("m&", 1),
    ("m^", 2),
    ("m^x", 2),
    ("m^7", 2),
    ("m^-7", 3),
    ("m^60", 2),
    ("m**s", 2),
    ("m//s", 2),
    ("m^2^3", 3),
    ("(m]", 2),
])
def test_syntax_error_positions(expr, pos):
    with pytest.raises(UnitSyntaxError) as exc_info:
        convert("1", expr, "m")
    assert exc_info.value.position == pos
    with pytest.raises(UnitSyntaxError) as exc_info2:
        convert("1", "m", expr)
    assert exc_info2.value.position == pos


@pytest.mark.parametrize("expr", [
    "m^6", "m^-6", "m^+3", "m^0", "m ^ -2", "min", "mm", "kg",
])
def test_valid_expressions(expr):
    # 与自身换算恒为 1
    assert convert("1", expr, expr) == "1"


# ---------------------------------------------------------------- value 校验

@pytest.mark.parametrize("bad", [
    "", "abc", "1.2.3", "inf", "-inf", "nan", "1e", "1e+", "1 000", "0x10",
    "1,5", "e3", ".", "+", "1e10000",
])
def test_invalid_value(bad):
    with pytest.raises(InvalidValueError):
        parse_value(bad)


@pytest.mark.parametrize("value,expected", [
    ("1e3", "1000"),
    ("1.5e-3", "0.0015"),
    ("+2.5", "2.5"),
    (".5", "0.5"),
    ("5.", "5"),
    ("-0", "0"),
])
def test_valid_value_forms(value, expected):
    assert convert(value, "m", "m") == expected


# ---------------------------------------------------------------- 舍入与格式化

@pytest.mark.parametrize("value,frm,to,expected", [
    # 无限循环小数：ROUND_HALF_EVEN 到 12 位
    ("1", "s", "h", "0.000277777778"),
    ("2", "s", "h", "0.000555555556"),
    ("1", "g", "kg", "0.001"),
    # 恰好在第 13 位的“半”：向偶数舍入
    ("0.0000000000005", "m", "m", "0"),    # ...0|5，前位 0 为偶 → 舍
    ("0.0000000000015", "m", "m", "0.000000000002"),  # 前位 1 为奇 → 入
    ("0.0000000000025", "m", "m", "0.000000000002"),  # 前位 2 为偶 → 舍
    ("0.0000000000135", "m", "m", "0.000000000014"),  # 前位 3 为奇 → 入
    ("-0.0000000000015", "m", "m", "-0.000000000002"),
    # 去掉末尾零
    ("1.5000", "km", "m", "1500"),
    ("2.50", "m", "m", "2.5"),
    ("0.10", "m", "m", "0.1"),
    ("3", "km", "m", "3000"),
    # 负零不出现
    ("-0.0000000000001", "m", "m", "0"),
    ("-1.5", "km", "m", "-1500"),
])
def test_rounding_and_format(value, frm, to, expected):
    assert convert(value, frm, to) == expected


def test_format_result_strips_zeros():
    from fractions import Fraction
    assert format_result(Fraction(1, 3)) == "0.333333333333"
    assert format_result(Fraction(2, 3)) == "0.666666666667"
    assert format_result(Fraction(-1, 3)) == "-0.333333333333"
    assert format_result(Fraction(0)) == "0"
    assert format_result(Fraction(1000)) == "1000"
