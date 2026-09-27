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

def test_rectangle_area():
    r = propagate([f("2", "0.01", "m"), f("3", "0.02", "m")], "m^2")
    assert r.status_code == 200
    assert r.json() == {"value": "6", "uncertainty": "0.05"}


def test_result_is_string():
    r = propagate([f("10", "0.1", "cm", 3)], "m^3")
    assert r.status_code == 200
    body = r.json()
    assert body == {"value": "0.001", "uncertainty": "0.00003"}
    assert isinstance(body["value"], str)
    assert isinstance(body["uncertainty"], str)


def test_default_power_is_one():
    r = propagate([f("2", "0.01", "km")], "m")
    assert r.status_code == 200
    assert r.json() == {"value": "2000", "uncertainty": "10"}


def test_dimensionless_target():
    r = propagate([f("2", "0.02", "m"), f("50", "0.5", "cm", -1)], "1")
    assert r.status_code == 200
    assert r.json() == {"value": "4", "uncertainty": "0.056568542495"}


def test_zero_value_zero_power_counts_as_one():
    r = propagate([f("0", "5", "m", 0), f("2", "0.1", "s")], "s")
    assert r.status_code == 200
    assert r.json() == {"value": "2", "uncertainty": "0.1"}


def test_eight_factors():
    # 目标单位的幂同样受 ±6 限制，m^8 需写成复合式
    r = propagate([f("1", "0.1", "m") for _ in range(8)], "m^6*m^2")
    assert r.status_code == 200
    # u = √(8·0.1²) = √0.08
    assert r.json() == {"value": "1", "uncertainty": "0.282842712475"}


# ---------------------------------------------------------------- 422：数值

def test_invalid_value():
    for field, bad in (("value", "abc"), ("uncertainty", "1.2.3")):
        factor = f("1", "0.1", "m")
        factor[field] = bad
        r = propagate([factor], "m")
        assert r.status_code == 422, (field, bad)
        detail = r.json()["detail"]
        assert detail["error"] == "invalid_value"
        assert detail["factor"] == 0
        assert detail["field"] == field


def test_negative_uncertainty():
    r = propagate([f("1", "0.1", "m"), f("2", "-0.5", "m")], "m^2")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "negative_uncertainty"
    assert detail["factor"] == 1


def test_zero_value_negative_power():
    r = propagate([f("0", "0.1", "m", -1)], "m")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "zero_negative_power"
    assert detail["factor"] == 0


# ---------------------------------------------------------------- 422：单位

def test_offset_temperature_rejected():
    for unit in ("°C", "°F"):
        r = propagate([f("1", "0.1", unit)], "K")
        assert r.status_code == 422, unit
        detail = r.json()["detail"]
        assert detail["error"] == "offset_temperature_not_allowed"
        assert detail["factor"] == 0


def test_offset_temperature_in_compound_rejected():
    r = propagate([f("1", "0.1", "m"), f("2", "0.1", "K*°C")], "m*K")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "offset_temperature_not_allowed"
    assert detail["factor"] == 1


def test_offset_temperature_target_rejected():
    r = propagate([f("1", "0.1", "K")], "°F")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "offset_temperature_not_allowed"
    assert detail["field"] == "to"


def test_unit_syntax_error_has_factor_and_position():
    r = propagate([f("1", "0.1", "m"), f("2", "0.1", "kg&")], "m*kg")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "syntax_error"
    assert detail["factor"] == 1
    assert detail["position"] == 2


def test_target_syntax_error_has_field_to():
    r = propagate([f("1", "0.1", "m")], "m*")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "syntax_error"
    assert detail["field"] == "to"
    assert detail["position"] == 2


def test_factor_unit_one_is_syntax_error():
    # "1" 仅是目标单位的无量纲记号，因子单位不接受
    r = propagate([f("1", "0.1", "1")], "1")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "syntax_error"


def test_dimension_mismatch():
    r = propagate([f("1", "0.1", "m")], "s")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "dimension_mismatch"
    # 无量纲目标只接受无量纲乘积
    r = propagate([f("1", "0.1", "m")], "1")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "dimension_mismatch"


# ---------------------------------------------------------------- 422：请求体结构

def test_missing_to():
    r = client.post("/propagate-product",
                    json={"factors": [f("1", "0.1", "m")]})
    assert r.status_code == 422


def test_empty_factors():
    assert propagate([], "m").status_code == 422


def test_too_many_factors():
    r = propagate([f("1", "0.1", "m") for _ in range(9)], "m")
    assert r.status_code == 422


def test_power_out_of_range():
    assert propagate([f("1", "0.1", "m", 7)], "m").status_code == 422
    assert propagate([f("1", "0.1", "m", -7)], "m").status_code == 422


def test_missing_factor_fields():
    assert propagate([{"uncertainty": "0.1", "unit": "m"}], "m").status_code == 422
    assert propagate([{"value": "1", "unit": "m"}], "m").status_code == 422
    assert propagate([{"value": "1", "uncertainty": "0.1"}], "m").status_code == 422


def test_unknown_field_rejected():
    factor = f("1", "0.1", "m")
    factor["note"] = "nope"
    assert propagate([factor], "m").status_code == 422
