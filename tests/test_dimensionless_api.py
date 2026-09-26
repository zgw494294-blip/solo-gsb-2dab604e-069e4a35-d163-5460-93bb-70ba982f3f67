"""POST /dimensionless-group 的 HTTP API 测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def group(quantities):
    return client.post("/dimensionless-group", json={"quantities": quantities})


def q(name, unit):
    return {"name": name, "unit": unit}


# ---------------------------------------------------------------- 成功

def test_reynolds_number():
    # 雷诺数 Re = rho * v * L / mu
    r = group([q("rho", "kg/m^3"), q("v", "m/s"),
               q("L", "m"), q("mu", "kg/(m*s)")])
    assert r.status_code == 200
    assert r.json() == {"exponents": {"L": 1, "mu": -1, "rho": 1, "v": 1}}


def test_response_sorted_by_name_and_includes_zero_exponents():
    # 三个量纲相同的量：L1 最小的解不唯一，按字典序选 (0, 1, -1)
    r = group([q("c", "cm"), q("a", "m"), q("b", "km")])
    assert r.status_code == 200
    body = r.json()
    assert list(body["exponents"]) == ["a", "b", "c"]
    assert body["exponents"] == {"a": 0, "b": 1, "c": -1}


def test_single_dimensionless_quantity():
    r = group([q("pi", "m/m")])
    assert r.status_code == 200
    assert r.json() == {"exponents": {"pi": 1}}


def test_sign_convention():
    # force = mass * accel：首个非零指数必须为正
    r = group([q("force", "kg*m/s^2"), q("mass", "kg"), q("accel", "m/s^2")])
    assert r.status_code == 200
    assert r.json() == {"exponents": {"accel": 1, "force": -1, "mass": 1}}


def test_temperature_via_kelvin():
    r = group([q("x", "K"), q("y", "K")])
    assert r.status_code == 200
    assert r.json() == {"exponents": {"x": 1, "y": -1}}


def test_exponent_at_boundary():
    r = group([q("a", "m"), q("b", "m^6")])
    assert r.status_code == 200
    assert r.json() == {"exponents": {"a": 6, "b": -1}}


def test_eight_quantities():
    # 8 个无量纲量：字典序最小的 L1=1 解把指数 1 放在最后
    r = group([q(name, "m/m") for name in "abcdefgh"])
    assert r.status_code == 200
    assert r.json() == {"exponents": {
        "a": 0, "b": 0, "c": 0, "d": 0, "e": 0, "f": 0, "g": 0, "h": 1}}


# ---------------------------------------------------------------- 422：无解

def test_no_solution_single_quantity():
    r = group([q("x", "m")])
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "no_dimensionless_group"


def test_no_solution_independent_dimensions():
    r = group([q("x", "m"), q("y", "s")])
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "no_dimensionless_group"


def test_no_solution_exponent_range_exceeded():
    r = group([q("a", "m"), q("b", "m*m*m*m*m*m*m")])
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "no_dimensionless_group"


# ---------------------------------------------------------------- 422：单位

def test_offset_temperature_rejected():
    for unit in ("°C", "°F"):
        r = group([q("t", unit)])
        assert r.status_code == 422, unit
        detail = r.json()["detail"]
        assert detail["error"] == "offset_temperature_not_allowed"
        assert detail["quantity"] == 0


def test_offset_temperature_in_compound_rejected():
    r = group([q("a", "m"), q("t", "K/°C")])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "offset_temperature_not_allowed"
    assert detail["quantity"] == 1


def test_unit_syntax_error_has_quantity_and_position():
    r = group([q("a", "m"), q("b", "kg&")])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "syntax_error"
    assert detail["quantity"] == 1
    assert detail["position"] == 2


# ---------------------------------------------------------------- 422：请求体结构

def test_missing_quantities():
    assert client.post("/dimensionless-group", json={}).status_code == 422


def test_empty_quantities():
    assert group([]).status_code == 422


def test_too_many_quantities():
    r = group([q(f"n{i}", "m") for i in range(9)])
    assert r.status_code == 422


def test_blank_name_rejected():
    assert group([q("   ", "m")]).status_code == 422


def test_duplicate_names_rejected():
    r = group([q("a", "m"), q("a", "s")])
    assert r.status_code == 422


def test_missing_name_or_unit():
    assert group([{"unit": "m"}]).status_code == 422
    assert group([{"name": "a"}]).status_code == 422


def test_unknown_field_rejected():
    r = client.post("/dimensionless-group", json={
        "quantities": [{"name": "a", "unit": "m", "note": "nope"}]})
    assert r.status_code == 422
