"""HTTP API 测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(value, frm, to):
    return client.post("/convert", json={"value": value, "from": frm, "to": to})


def test_convert_ok():
    r = post("1", "km", "m")
    assert r.status_code == 200
    assert r.json() == {"result": "1000"}
    assert isinstance(r.json()["result"], str)


def test_convert_expression():
    r = post("36", "km/h", "m/s")
    assert r.status_code == 200
    assert r.json()["result"] == "10"


def test_convert_temperature():
    assert post("100", "°C", "°F").json()["result"] == "212"
    assert post("32", "°F", "K").json()["result"] == "273.15"
    assert post("273.15", "K", "°C").json()["result"] == "0"


def test_result_is_string_for_large_and_small():
    assert post("1", "m^6", "mm^6").json()["result"] == "1000000000000000000"
    assert post("1", "mm^4", "m^4").json()["result"] == "0.000000000001"
    # 小于 12 位小数精度的结果按 ROUND_HALF_EVEN 舍入为 0
    assert post("1", "mm^6", "m^6").json()["result"] == "0"


def test_syntax_error_has_zero_based_position():
    r = post("1", "m s", "m")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "syntax_error"
    assert detail["field"] == "from"
    assert detail["position"] == 2

    r = post("1", "m", "kg*")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "syntax_error"
    assert detail["field"] == "to"
    assert detail["position"] == 3


def test_syntax_error_exponent_out_of_range():
    r = post("1", "m^7", "m")
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "syntax_error"
    assert r.json()["detail"]["position"] == 2


def test_dimension_mismatch():
    r = post("1", "m", "s")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "dimension_mismatch"
    assert "length" in detail["message"]


def test_offset_temperature_in_compound():
    for expr in ("°C*m", "m*°F", "°C^2", "°F/s"):
        r = post("1", expr, "K")
        assert r.status_code == 422, expr
        assert r.json()["detail"]["error"] == "offset_temperature_in_compound"
        r = post("1", "K", expr)
        assert r.status_code == 422, expr
        assert r.json()["detail"]["error"] == "offset_temperature_in_compound"


def test_offset_temperature_standalone_ok():
    r = post("0", "(°C)", "K")
    assert r.status_code == 200
    assert r.json()["result"] == "273.15"


def test_invalid_value():
    for bad in ("abc", "1.2.3", "inf", "nan", ""):
        r = post(bad, "m", "m")
        assert r.status_code == 422, bad
        assert r.json()["detail"]["error"] == "invalid_value"


def test_missing_or_wrong_fields_422():
    assert client.post("/convert", json={"value": "1", "from": "m"}).status_code == 422
    assert client.post("/convert", json={}).status_code == 422
    assert client.post("/convert", json={"value": 1, "from": "m", "to": "m"}).status_code == 422


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
