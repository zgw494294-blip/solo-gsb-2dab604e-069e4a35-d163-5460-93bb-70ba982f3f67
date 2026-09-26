"""量纲推断核心逻辑测试（不经过 HTTP）。"""

import pytest

from app.dimensions import (
    EquationInput,
    FactorInput as F,
    FractionalSolutionError,
    InconsistentConstraintsError,
    NoVariableError,
    UnderdeterminedError,
    UnitFactorError,
    infer_dimensions,
)


def E(left, right=None) -> EquationInput:
    """便捷构造：参数为 (kind, name[, power]) 三元组或二元组。"""
    def factors(items):
        out = []
        for item in items:
            kind, name, *rest = item
            power = rest[0] if rest else 1
            if kind == "var":
                out.append(F(var=name, power=power))
            else:
                out.append(F(unit=name, power=power))
        return tuple(out)
    return EquationInput(factors(left), factors(right or []))


# ---------------------------------------------------------------- 成功推断

def test_single_variable_against_unit():
    assert infer_dimensions([E([("var", "x")], [("unit", "m")])]) == {
        "x": (1, 0, 0, 0)}


def test_newton_second_law():
    # F = m*a；a = m/s^2；m = kg
    result = infer_dimensions([
        E([("var", "F")], [("var", "m"), ("var", "a")]),
        E([("var", "a")], [("unit", "m/s^2")]),
        E([("var", "m")], [("unit", "kg")]),
    ])
    assert result == {
        "F": (1, 1, -2, 0),
        "m": (0, 1, 0, 0),
        "a": (1, 0, -2, 0),
    }


def test_kinetic_energy_dimensions():
    # energy = mass * v^2；v = m/s；mass = kg
    result = infer_dimensions([
        E([("var", "energy")],
          [("var", "mass"), ("var", "v"), ("var", "v")]),
        E([("var", "v")], [("unit", "m/s")]),
        E([("var", "mass")], [("unit", "kg")]),
    ])
    assert result == {
        "energy": (2, 1, -2, 0),
        "mass": (0, 1, 0, 0),
        "v": (1, 0, -1, 0),
    }


def test_temperature_dimension():
    assert infer_dimensions([E([("var", "T")], [("unit", "K")])]) == {
        "T": (0, 0, 0, 1)}


def test_compound_unit_with_powers():
    # pressure P = kg/(m*s^2)
    result = infer_dimensions([E([("var", "P")], [("unit", "kg/(m*s^2)")])])
    assert result == {"P": (-1, 1, -2, 0)}


def test_default_power_is_one():
    result = infer_dimensions([
        E([("var", "x")], [("unit", "m"), ("unit", "s", -1)])])
    assert result == {"x": (1, 0, -1, 0)}


def test_negative_power_factors():
    # x/m = 1 → x 为长度
    result = infer_dimensions([E([("var", "x"), ("unit", "m", -1)], [])])
    assert result == {"x": (1, 0, 0, 0)}
    # x^-2 = s^-2 → x 为时间
    result = infer_dimensions([
        E([("var", "x", -2)], [("unit", "s^-2")])])
    assert result == {"x": (0, 0, 1, 0)}


def test_repeated_factors_merge():
    result = infer_dimensions([
        E([("var", "x"), ("var", "x")],
          [("unit", "m"), ("unit", "m")])])
    assert result == {"x": (1, 0, 0, 0)}


def test_right_side_variables():
    # 1 = x/m → x 为长度（变量只出现在右侧）
    result = infer_dimensions([E([], [("var", "x"), ("unit", "m", -1)])])
    assert result == {"x": (1, 0, 0, 0)}


def test_redundant_equations_still_unique():
    result = infer_dimensions([
        E([("var", "x")], [("unit", "m")]),
        E([("var", "x")], [("unit", "m")]),
    ])
    assert result == {"x": (1, 0, 0, 0)}


def test_dimensionless_variable():
    # ratio = m/km（两侧同为长度，但 ratio 本身无量纲）
    result = infer_dimensions([
        E([("var", "ratio"), ("unit", "km")], [("unit", "m")])])
    assert result == {"ratio": (0, 0, 0, 0)}


# ---------------------------------------------------------------- 欠定

def test_underdetermined_two_vars_one_equation():
    with pytest.raises(UnderdeterminedError) as exc:
        infer_dimensions([E([("var", "x"), ("var", "y")], [("unit", "m")])])
    assert exc.value.extra["free_variables"] == ["y"]


def test_underdetermined_chain_missing_anchor():
    # x=y, y=z：秩 2，三个变量均欠定
    with pytest.raises(UnderdeterminedError) as exc:
        infer_dimensions([
            E([("var", "x")], [("var", "y")]),
            E([("var", "y")], [("var", "z")]),
        ])
    assert exc.value.extra["free_variables"] == ["z"]


def test_underdetermined_redundant_rows():
    with pytest.raises(UnderdeterminedError) as exc:
        infer_dimensions([
            E([("var", "x"), ("var", "y")], [("unit", "m")]),
            E([("var", "x"), ("var", "y")], [("unit", "m")]),
        ])
    assert exc.value.extra["free_variables"] == ["y"]


# ---------------------------------------------------------------- 矛盾

def test_contradictory_anchors():
    with pytest.raises(InconsistentConstraintsError) as exc:
        infer_dimensions([
            E([("var", "x")], [("unit", "m")]),
            E([("var", "x")], [("unit", "s")]),
        ])
    assert exc.value.extra["dimension"] == "length"


def test_contradiction_from_unit_only_equation():
    # m = s 本身就是矛盾，即使 x 另有约束
    with pytest.raises(InconsistentConstraintsError):
        infer_dimensions([
            E([("var", "x")], [("unit", "m")]),
            E([("unit", "m")], [("unit", "s")]),
        ])


def test_contradiction_takes_precedence_over_underdetermination():
    # 秩亏缺与矛盾同时出现时，必须报矛盾
    with pytest.raises(InconsistentConstraintsError):
        infer_dimensions([
            E([("var", "x"), ("var", "y")], [("unit", "m")]),
            E([("unit", "kg")], [("unit", "s")]),
        ])


# ---------------------------------------------------------------- 分数解

def test_fractional_solution_square_root():
    with pytest.raises(FractionalSolutionError) as exc:
        infer_dimensions([E([("var", "x", 2)], [("unit", "m")])])
    assert exc.value.extra["variables"] == ["x"]


def test_fractional_solution_negative_exponent():
    # x^2 = s^-1 → x = s^-1/2
    with pytest.raises(FractionalSolutionError) as exc:
        infer_dimensions([E([("var", "x", 2)], [("unit", "s^-1")])])
    assert exc.value.extra["variables"] == ["x"]


def test_fractional_but_unique_two_variables():
    # 2x = L, 2y = M（用 x^2=m 与 y^2=kg 构造）
    with pytest.raises(FractionalSolutionError) as exc:
        infer_dimensions([
            E([("var", "x", 2)], [("unit", "m")]),
            E([("var", "y", 2)], [("unit", "kg")]),
        ])
    assert exc.value.extra["variables"] == ["x", "y"]


# ---------------------------------------------------------------- 输入错误

def test_no_variable():
    with pytest.raises(NoVariableError):
        infer_dimensions([E([("unit", "m")], [("unit", "m")])])


def test_offset_temperature_rejected():
    for expr in ("°C", "°F"):
        with pytest.raises(UnitFactorError) as exc:
            infer_dimensions([E([("var", "x")], [("unit", expr)])])
        assert exc.value.kind == "offset_temperature_not_allowed"


def test_unit_syntax_error_is_wrapped():
    with pytest.raises(UnitFactorError) as exc:
        infer_dimensions([E([("var", "x")], [("unit", "m s")])])
    assert exc.value.kind == "syntax_error"
    assert exc.value.position == 2


def test_unit_error_coordinates():
    with pytest.raises(UnitFactorError) as exc:
        infer_dimensions([
            E([("var", "x")], [("unit", "m")]),
            E([("var", "y")], [("unit", "kg&")]),
        ])
    err = exc.value
    assert err.equation == 1
    assert err.side == "right"
    assert err.factor == 0
    assert err.position == 2
