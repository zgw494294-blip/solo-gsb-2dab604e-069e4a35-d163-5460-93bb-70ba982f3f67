"""POST /infer-dimensions 的 HTTP API 测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def infer(equations):
    return client.post("/infer-dimensions", json={"equations": equations})


def factor(kind, name, **kw):
    return {kind: name, **kw}


# ---------------------------------------------------------------- 成功

def test_infer_single_variable():
    r = infer([
        {"left": [factor("var", "x")],
         "right": [factor("unit", "m/s")]},
    ])
    assert r.status_code == 200
    assert r.json() == {
        "dimensions": {"x": {"length": 1, "mass": 0,
                             "time": -1, "temperature": 0}}}


def test_infer_newton_and_sorted_keys():
    r = infer([
        {"left": [factor("var", "force")],
         "right": [factor("var", "mass"), factor("var", "accel")]},
        {"left": [factor("var", "accel")],
         "right": [factor("unit", "m/s^2")]},
        {"left": [factor("var", "mass")], "right": [factor("unit", "kg")]},
    ])
    assert r.status_code == 200
    body = r.json()
    # 按变量名排序
    assert list(body["dimensions"]) == ["accel", "force", "mass"]
    assert body["dimensions"]["force"] == {
        "length": 1, "mass": 1, "time": -2, "temperature": 0}


def test_infer_default_and_explicit_power():
    r = infer([
        {"left": [factor("var", "v", power=2)],
         "right": [factor("unit", "m^2"), factor("unit", "s", power=-2)]},
    ])
    assert r.status_code == 200
    assert r.json()["dimensions"]["v"] == {
        "length": 1, "mass": 0, "time": -1, "temperature": 0}


def test_infer_temperature_via_kelvin():
    r = infer([
        {"left": [factor("var", "T")], "right": [factor("unit", "K")]},
    ])
    assert r.status_code == 200
    assert r.json()["dimensions"]["T"]["temperature"] == 1


def test_dimensionless_variable():
    r = infer([
        {"left": [factor("var", "ratio"), factor("unit", "km")],
         "right": [factor("unit", "m")]},
    ])
    assert r.status_code == 200
    assert r.json()["dimensions"]["ratio"] == {
        "length": 0, "mass": 0, "time": 0, "temperature": 0}


# ---------------------------------------------------------------- 422：约束问题

def test_underdetermined():
    r = infer([
        {"left": [factor("var", "x"), factor("var", "y")],
         "right": [factor("unit", "m")]},
    ])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "underdetermined"
    assert detail["free_variables"] == ["y"]


def test_inconsistent():
    r = infer([
        {"left": [factor("var", "x")], "right": [factor("unit", "m")]},
        {"left": [factor("var", "x")], "right": [factor("unit", "s")]},
    ])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "inconsistent_constraints"
    assert detail["dimension"] == "length"


def test_inconsistent_unit_only_equation():
    r = infer([
        {"left": [factor("var", "x")], "right": [factor("unit", "m")]},
        {"left": [factor("unit", "m")], "right": [factor("unit", "s")]},
    ])
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "inconsistent_constraints"


def test_fractional_solution():
    r = infer([
        {"left": [factor("var", "x", power=2)],
         "right": [factor("unit", "m")]},
    ])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "fractional_dimensions"
    assert detail["variables"] == ["x"]


def test_no_variable():
    r = infer([
        {"left": [factor("unit", "m")], "right": [factor("unit", "m")]},
    ])
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "no_variable"


# ---------------------------------------------------------------- 422：单位因子

def test_offset_temperature_unit_rejected():
    for unit in ("°C", "°F"):
        r = infer([
            {"left": [factor("var", "x")], "right": [factor("unit", unit)]},
        ])
        assert r.status_code == 422, unit
        detail = r.json()["detail"]
        assert detail["error"] == "offset_temperature_not_allowed"
        assert detail["equation"] == 0
        assert detail["side"] == "right"
        assert detail["factor"] == 0


def test_offset_temperature_in_compound_rejected():
    r = infer([
        {"left": [factor("var", "x")],
         "right": [factor("unit", "K/°C")]},
    ])
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "offset_temperature_not_allowed"


def test_unit_syntax_error_has_coordinates_and_position():
    r = infer([
        {"left": [factor("var", "x")], "right": [factor("unit", "m")]},
        {"left": [factor("var", "y")], "right": [
            factor("var", "z"), factor("unit", "kg&")]},
    ])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "syntax_error"
    assert detail["equation"] == 1
    assert detail["side"] == "right"
    assert detail["factor"] == 1
    assert detail["position"] == 2


# ---------------------------------------------------------------- 422：请求体结构

def test_missing_equations():
    assert client.post("/infer-dimensions", json={}).status_code == 422


def test_empty_equations():
    assert infer([]).status_code == 422


def test_empty_side():
    r = infer([{"left": [], "right": [factor("unit", "m")]}])
    assert r.status_code == 422


def test_factor_must_have_exactly_one_field():
    # 两者都缺
    r = infer([{"left": [{"power": 1}], "right": [factor("unit", "m")]}])
    assert r.status_code == 422
    # 两者都有
    r = infer([{
        "left": [{"var": "x", "unit": "m"}],
        "right": [factor("unit", "m")]}])
    assert r.status_code == 422


def test_power_out_of_range():
    for bad in (-7, 7):
        r = infer([
            {"left": [factor("var", "x", power=bad)],
             "right": [factor("unit", "m")]},
        ])
        assert r.status_code == 422, bad


def test_power_must_be_integer():
    r = infer([
        {"left": [{"var": "x", "power": 1.5}],
         "right": [{"unit": "m"}]},
    ])
    assert r.status_code == 422


def test_unknown_field_rejected():
    r = client.post("/infer-dimensions", json={
        "equations": [
            {"left": [{"var": "x"}], "right": [{"unit": "m"}],
             "note": "nope"}],
    })
    assert r.status_code == 422


def test_blank_var_rejected():
    r = infer([
        {"left": [factor("var", "   ")], "right": [factor("unit", "m")]},
    ])
    assert r.status_code == 422
