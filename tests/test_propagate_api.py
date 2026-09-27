"""POST /propagate-product 的 HTTP API 测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def propagate(factors, to):
    return client.post("/propagate-product",
                       json={"factors": factors, "to": to})


def f(value, uncertainty, unit, power=None):
    factor = {"value": value, "uncertainty": uncertainty, "unit": unit}
    if power is not None:
        factor["power"] = power
    return factor


# ---------------------------------------------------------------- 成功

def test_area_with_uncertainty():
    # y = 2·3 = 6；u = √((3·0.1)² + (2·0.2)²) = 0.5
    r = propagate([f("2", "0.1", "m"), f("3", "0.2", "m")], "m^2")
    assert r.status_code == 200
    assert r.json() == {"value": "6", "uncertainty": "0.5"}
    assert isinstance(r.json()["value"], str)
    assert isinstance(r.json()["uncertainty"], str)


def test_factor_units_converted_to_base():
    r = propagate([f("2", "0.001", "km")], "m")
    assert r.status_code == 200
    assert r.json() == {"value": "2000", "uncertainty": "1"}


def test_result_converted_to_target():
    r = propagate([f("2", "0.1", "m"), f("3", "0.2", "m")], "cm^2")
    assert r.status_code == 200
    assert r.json() == {"value": "60000", "uncertainty": "5000"}


def test_power_and_default_power():
    r = propagate([f("2", "0.1", "m/s", power=2)], "(m/s)^2")
    assert r.status_code == 200
    assert r.json() == {"value": "4", "uncertainty": "0.4"}
    # 缺省 power 为 1
    r = propagate([f("5", "0.5", "kg")], "kg")
    assert r.json() == {"value": "5", "uncertainty": "0.5"}


def test_negative_power():
    r = propagate([f("2", "0.1", "s", power=-1)], "s^-1")
    assert r.status_code == 200
    assert r.json() == {"value": "0.5", "uncertainty": "0.025"}


def test_zero_to_zero_power_treated_as_one():
    r = propagate([f("0", "0.5", "m", power=0), f("2", "0.1", "m")], "m")
    assert r.status_code == 200
    assert r.json() == {"value": "2", "uncertainty": "0.1"}


def test_zero_factor_positive_power():
    # y = 0；∂y/∂x_0 = 1·0⁰·2 = 2 → u = 1
    r = propagate([f("0", "0.5", "m"), f("2", "0.1", "m")], "m^2")
    assert r.status_code == 200
    assert r.json() == {"value": "0", "uncertainty": "1"}


def test_dimensionless_target_one():
    r = propagate([f("2", "0.1", "m/m")], "1")
    assert r.status_code == 200
    assert r.json() == {"value": "2", "uncertainty": "0.1"}


def test_kelvin_factor_ok():
    r = propagate([f("300", "1.5", "K")], "K")
    assert r.status_code == 200
    assert r.json() == {"value": "300", "uncertainty": "1.5"}


def test_uncertainty_rounding_irrational():
    # u = √2 = 1.4142135623730…
    r = propagate([f("1", "1", "m"), f("1", "1", "m")], "m^2")
    assert r.status_code == 200
    assert r.json() == {"value": "1", "uncertainty": "1.414213562373"}


def test_uncertainty_rounding_half_even_tie():
    r = propagate([f("1", "0.0000000000015", "m")], "m")
    assert r.json()["uncertainty"] == "0.000000000002"
    r = propagate([f("1", "0.0000000000025", "m")], "m")
    assert r.json()["uncertainty"] == "0.000000000002"


def test_negative_value():
    r = propagate([f("-2", "0.1", "m", power=3)], "m^3")
    assert r.status_code == 200
    assert r.json() == {"value": "-8", "uncertainty": "1.2"}


def test_eight_factors():
    # 单位幂次上限为 ±6，m^8 需写成乘积形式
    r = propagate([f("1", "0", "m")] * 8, "m*m*m*m*m*m*m*m")
    assert r.status_code == 200
    assert r.json() == {"value": "1", "uncertainty": "0"}


def test_compound_dimensions_accumulate():
    r = propagate([f("2", "0.1", "m"), f("3", "0.2", "kg")], "kg*m")
    assert r.status_code == 200
    assert r.json() == {"value": "6", "uncertainty": "0.5"}


# ---------------------------------------------------------------- 422：数值

def test_invalid_value():
    for bad in ("abc", "", "1.2.3", "nan", "inf"):
        r = propagate([f(bad, "0.1", "m")], "m")
        assert r.status_code == 422, bad
        detail = r.json()["detail"]
        assert detail["error"] == "invalid_value"
        assert detail["factor"] == 0


def test_invalid_uncertainty():
    for bad in ("abc", "", "-0.1", "-1", "1e10000"):
        r = propagate([f("1", bad, "m")], "m")
        assert r.status_code == 422, bad
        detail = r.json()["detail"]
        assert detail["error"] == "invalid_uncertainty"
        assert detail["factor"] == 0


def test_factor_index_reported():
    r = propagate([f("1", "0.1", "m"), f("x", "0.1", "m")], "m^2")
    assert r.status_code == 422
    assert r.json()["detail"]["factor"] == 1


# ---------------------------------------------------------------- 422：单位与量纲

def test_offset_temperature_factor_rejected():
    for unit in ("°C", "°F", "K*°C", "°F^2"):
        r = propagate([f("1", "0.1", unit)], "K")
        assert r.status_code == 422, unit
        detail = r.json()["detail"]
        assert detail["error"] == "offset_temperature_not_allowed"
        assert detail["factor"] == 0


def test_offset_temperature_target_rejected():
    for to in ("°C", "°F", "m*°C"):
        r = propagate([f("1", "0.1", "K")], to)
        assert r.status_code == 422, to
        detail = r.json()["detail"]
        assert detail["error"] == "offset_temperature_not_allowed"
        assert detail["field"] == "to"


def test_zero_negative_power_rejected():
    r = propagate([f("0", "0.1", "m", power=-1)], "m^-1")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "zero_negative_power"
    assert detail["factor"] == 0


def test_dimension_mismatch():
    r = propagate([f("1", "0.1", "m")], "s")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "dimension_mismatch"
    # 无量纲目标与有量纲乘积同样不符
    r = propagate([f("1", "0.1", "m")], "1")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "dimension_mismatch"


def test_unit_syntax_error_has_factor_and_position():
    r = propagate([f("1", "0.1", "m"), f("1", "0.1", "kg&")], "m*kg")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "syntax_error"
    assert detail["factor"] == 1
    assert detail["position"] == 2


def test_target_syntax_error_has_field_and_position():
    r = propagate([f("1", "0.1", "m")], "m*")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "syntax_error"
    assert detail["field"] == "to"
    assert detail["position"] == 2


# ---------------------------------------------------------------- 422：请求体结构

def test_missing_fields_422():
    assert client.post("/propagate-product", json={}).status_code == 422
    assert client.post("/propagate-product",
                       json={"factors": [f("1", "0.1", "m")]}).status_code == 422
    assert client.post("/propagate-product",
                       json={"to": "m"}).status_code == 422


def test_factor_count_bounds():
    assert propagate([], "1").status_code == 422
    assert propagate([f("1", "0.1", "m/m")] * 9, "1").status_code == 422


def test_power_bounds():
    assert propagate([f("1", "0.1", "m", power=7)], "m^7").status_code == 422
    assert propagate([f("1", "0.1", "m", power=-7)], "m^-7").status_code == 422
    assert propagate([f("1", "0.1", "m", power=6)], "m^6").status_code == 200


def test_missing_factor_fields():
    assert propagate([{"value": "1", "unit": "m"}], "m").status_code == 422
    assert propagate([{"uncertainty": "0.1", "unit": "m"}], "m").status_code == 422
    assert propagate([{"value": "1", "uncertainty": "0.1"}], "m").status_code == 422


def test_unknown_field_rejected():
    r = client.post("/propagate-product", json={
        "factors": [{"value": "1", "uncertainty": "0.1", "unit": "m",
                     "note": "nope"}],
        "to": "m"})
    assert r.status_code == 422
