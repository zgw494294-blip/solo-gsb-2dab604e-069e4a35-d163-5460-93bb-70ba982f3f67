"""单位换算核心：表达式解析、量纲核对、有理数比例换算与结果格式化。

- 单位表达式支持括号、``*``、``/`` 以及 ``^`` 后的 -6～6 整数幂；
  忽略空白，不允许隐式乘法。
- 量纲按（长度、质量、时间、温度）四维向量核对。
- 比例一律用 ``Fraction``（有理数）累计，温度偏移量同样以有理数精确表示。
- ``°C`` / ``°F`` 为偏移温标，只允许单独使用（可套括号）；
  换算先经 K（°C + 273.15 = K，(°F + 459.67) × 5/9 = K），
  复合式中的 K 只按比例处理。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from math import isqrt

# Python 3.11+ 对 int->str 有 4300 位上限；换算结果可能更长，提前放宽。
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(1_000_000)
# 括号嵌套深度受表达式长度限制（max_length=2000），放宽递归上限即可。
sys.setrecursionlimit(10_000)


# ---------------------------------------------------------------------------
# 错误类型
# ---------------------------------------------------------------------------

class UnitSyntaxError(Exception):
    """单位表达式语法错误；position 为从 0 开始的字符位置。"""

    def __init__(self, message: str, position: int):
        super().__init__(message)
        self.message = message
        self.position = position


class DimensionMismatchError(Exception):
    """from 与 to 的量纲不一致。"""


class OffsetScaleInCompoundError(Exception):
    """偏移温标（°C / °F）参与了复合运算。"""

    def __init__(self, message: str, position: int | None = None):
        super().__init__(message)
        self.position = position


class InvalidValueError(Exception):
    """value 不是有限十进制字符串。"""


# ---------------------------------------------------------------------------
# 单位表
# ---------------------------------------------------------------------------

# 量纲向量顺序：长度、质量、时间、温度
DIM_NAMES = ("length", "mass", "time", "temperature")

# 符号 -> (量纲向量, 相对基本单位 m/kg/s/K 的比例，有理数)
UNITS: dict[str, tuple[tuple[int, int, int, int], Fraction]] = {
    "m":   ((1, 0, 0, 0), Fraction(1)),
    "km":  ((1, 0, 0, 0), Fraction(1000)),
    "cm":  ((1, 0, 0, 0), Fraction(1, 100)),
    "mm":  ((1, 0, 0, 0), Fraction(1, 1000)),
    "s":   ((0, 0, 1, 0), Fraction(1)),
    "min": ((0, 0, 1, 0), Fraction(60)),
    "h":   ((0, 0, 1, 0), Fraction(3600)),
    "kg":  ((0, 1, 0, 0), Fraction(1)),
    "g":   ((0, 1, 0, 0), Fraction(1, 1000)),
    "K":   ((0, 0, 0, 1), Fraction(1)),
}

# 偏移温标：只允许单独使用，换算先经 K
OFFSET_UNITS = ("°C", "°F")

# 温度偏移的有理数精确值：°C + 273.15 = K；(°F + 459.67) × 5/9 = K
CELSIUS_OFFSET = Fraction(5463, 20)       # 273.15
FAHRENHEIT_OFFSET = Fraction(45967, 100)  # 459.67

# 贪心最长匹配：min/kg/km/cm/mm/°C/°F 须优先于 m/g/K 等单字符单位
_ALL_SYMBOLS = sorted([*UNITS, *OFFSET_UNITS], key=len, reverse=True)


# ---------------------------------------------------------------------------
# 词法分析
# ---------------------------------------------------------------------------

@dataclass
class Token:
    kind: str      # "unit" | "pow" | "(" | ")" | "*" | "/"
    text: str
    pos: int       # 从 0 开始的字符位置
    power: int = 0  # kind == "pow" 时的整数幂


def _scan_power(expr: str, start: int) -> tuple[Token, int]:
    """扫描 ``^`` 及其后的整数幂（可带正负号，范围 -6～6）。"""
    n = len(expr)
    j = start + 1
    while j < n and expr[j].isspace():
        j += 1
    sign = 1
    if j < n and expr[j] in "+-":
        if expr[j] == "-":
            sign = -1
        j += 1
        while j < n and expr[j].isspace():
            j += 1
    k = j
    while k < n and "0" <= expr[k] <= "9":
        k += 1
    if k == j:
        raise UnitSyntaxError("expected an integer exponent after '^'", j)
    power = sign * int(expr[j:k])
    if power < -6 or power > 6:
        raise UnitSyntaxError(f"exponent {power} out of range (-6 to 6)", j)
    return Token("pow", expr[start:k], start, power), k


def tokenize(expr: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "()*/":
            tokens.append(Token(ch, ch, i))
            i += 1
            continue
        if ch == "^":
            token, i = _scan_power(expr, i)
            tokens.append(token)
            continue
        for sym in _ALL_SYMBOLS:
            if expr.startswith(sym, i):
                tokens.append(Token("unit", sym, i))
                i += len(sym)
                break
        else:
            raise UnitSyntaxError(f"unexpected character {ch!r}", i)
    return tokens


# ---------------------------------------------------------------------------
# 语法分析（递归下降）
# ---------------------------------------------------------------------------
# expression := product
# product    := factor (("*" | "/") factor)*
# factor     := atom pow?
# atom       := unit | "(" product ")"

# AST 节点：("unit", symbol, pos) | ("pow", node, power, pos)
#           | ("mul" | "div", left, right, pos)

class _Parser:
    def __init__(self, tokens: list[Token], expr_len: int):
        self._tokens = tokens
        self._i = 0
        self._len = expr_len

    def _peek(self) -> Token | None:
        return self._tokens[self._i] if self._i < len(self._tokens) else None

    def parse(self):
        node = self._product()
        tok = self._peek()
        if tok is not None:
            if tok.kind in ("unit", "("):
                raise UnitSyntaxError(
                    f"unexpected {tok.text!r} (missing operator; "
                    "implicit multiplication is not allowed)", tok.pos)
            raise UnitSyntaxError(f"unexpected {tok.text!r}", tok.pos)
        return node

    def _product(self):
        node = self._factor()
        while (tok := self._peek()) is not None and tok.kind in ("*", "/"):
            self._i += 1
            right = self._factor()
            node = ("mul" if tok.kind == "*" else "div", node, right, tok.pos)
        return node

    def _factor(self):
        node = self._atom()
        tok = self._peek()
        if tok is not None and tok.kind == "pow":
            self._i += 1
            node = ("pow", node, tok.power, tok.pos)
        return node

    def _atom(self):
        tok = self._peek()
        if tok is None:
            raise UnitSyntaxError("unexpected end of expression", self._len)
        if tok.kind == "unit":
            self._i += 1
            return ("unit", tok.text, tok.pos)
        if tok.kind == "(":
            self._i += 1
            node = self._product()
            closing = self._peek()
            if closing is None:
                raise UnitSyntaxError("missing closing parenthesis ')'", self._len)
            if closing.kind != ")":
                raise UnitSyntaxError(
                    f"expected ')' but found {closing.text!r}", closing.pos)
            self._i += 1
            return node
        raise UnitSyntaxError(f"unexpected {tok.text!r}", tok.pos)


# ---------------------------------------------------------------------------
# 求值：量纲向量 + 有理数比例
# ---------------------------------------------------------------------------

@dataclass
class Quantity:
    dims: tuple[int, int, int, int]
    scale: Fraction              # 相对基本单位的比例（有理数）
    offset_unit: str | None = None  # 单独出现的偏移温标符号（°C / °F）
    pos: int = 0                 # 偏移温标的字符位置（报错用）


def _evaluate(node) -> Quantity:
    kind = node[0]
    if kind == "unit":
        _, symbol, pos = node
        if symbol in OFFSET_UNITS:
            return Quantity((0, 0, 0, 1), Fraction(1), offset_unit=symbol, pos=pos)
        dims, scale = UNITS[symbol]
        return Quantity(dims, scale)
    if kind == "pow":
        _, child, power, _pos = node
        q = _evaluate(child)
        if q.offset_unit is not None:
            raise OffsetScaleInCompoundError(
                f"offset temperature scale {q.offset_unit!r} cannot be raised "
                "to a power; it may only be used standalone", q.pos)
        return Quantity(tuple(d * power for d in q.dims), q.scale ** power)
    # "mul" / "div"
    _, left, right, _pos = node
    lq, rq = _evaluate(left), _evaluate(right)
    for q in (lq, rq):
        if q.offset_unit is not None:
            raise OffsetScaleInCompoundError(
                f"offset temperature scale {q.offset_unit!r} cannot be used in "
                "a compound expression; it may only be used standalone", q.pos)
    if kind == "mul":
        return Quantity(tuple(a + b for a, b in zip(lq.dims, rq.dims)),
                        lq.scale * rq.scale)
    return Quantity(tuple(a - b for a, b in zip(lq.dims, rq.dims)),
                    lq.scale / rq.scale)


def parse_expression(expr: str) -> Quantity:
    """解析并求值单位表达式，返回其量纲与比例。"""
    return _evaluate(_Parser(tokenize(expr), len(expr)).parse())


# ---------------------------------------------------------------------------
# 数值解析与换算
# ---------------------------------------------------------------------------

_VALUE_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_EXPONENT_RE = re.compile(r"[eE]([+-]?\d+)$")
_MAX_EXPONENT = 9999  # 防止 1e999999999 之类的资源消耗攻击


def parse_value(raw: str) -> Fraction:
    """把有限十进制字符串解析为精确的有理数。"""
    if not _VALUE_RE.match(raw):
        raise InvalidValueError(f"invalid finite decimal string: {raw!r}")
    m = _EXPONENT_RE.search(raw)
    if m and abs(int(m.group(1))) > _MAX_EXPONENT:
        raise InvalidValueError(
            f"exponent out of supported range (±{_MAX_EXPONENT})")
    try:
        dec = Decimal(raw)
    except InvalidOperation:
        raise InvalidValueError(f"invalid finite decimal string: {raw!r}")
    if not dec.is_finite():
        raise InvalidValueError(f"value must be finite: {raw!r}")
    return Fraction(dec)


def _to_kelvin(value: Fraction, q: Quantity) -> Fraction:
    if q.offset_unit == "°C":
        return value + CELSIUS_OFFSET
    if q.offset_unit == "°F":
        return (value + FAHRENHEIT_OFFSET) * Fraction(5, 9)
    return value * q.scale  # K 及复合式只按比例处理


def _from_kelvin(kelvin: Fraction, q: Quantity) -> Fraction:
    if q.offset_unit == "°C":
        return kelvin - CELSIUS_OFFSET
    if q.offset_unit == "°F":
        return kelvin * Fraction(9, 5) - FAHRENHEIT_OFFSET
    return kelvin / q.scale


def dim_description(dims: tuple[int, int, int, int]) -> str:
    parts = [f"{name}^{d}" for name, d in zip(DIM_NAMES, dims) if d != 0]
    return " ".join(parts) if parts else "dimensionless"


def convert_value(value: Fraction, source: Quantity, target: Quantity) -> Fraction:
    """核对量纲后换算：比例式直接乘除，偏移温标先经 K。"""
    if source.dims != target.dims:
        raise DimensionMismatchError(
            f"dimension mismatch: {dim_description(source.dims)} "
            f"cannot be converted to {dim_description(target.dims)}")
    return _from_kelvin(_to_kelvin(value, source), target)


def convert(value_raw: str, from_expr: str, to_expr: str) -> str:
    """便捷入口：字符串进、字符串出。"""
    return format_result(convert_value(
        parse_value(value_raw), parse_expression(from_expr), parse_expression(to_expr)))


# ---------------------------------------------------------------------------
# 结果格式化：ROUND_HALF_EVEN，最多 12 位小数，去掉末尾零
# ---------------------------------------------------------------------------

MAX_DECIMALS = 12


def _format_scaled(q: int) -> str:
    """把 q（= 真值 × 10^MAX_DECIMALS 的整数）格式化为十进制字符串。

    去掉末尾零，最后经 Decimal 规范化输出。
    """
    if q == 0:
        return "0"
    negative = q < 0
    digits = str(abs(q)).rjust(MAX_DECIMALS + 1, "0")
    int_part = digits[:-MAX_DECIMALS]
    frac_part = digits[-MAX_DECIMALS:].rstrip("0")
    text = int_part if not frac_part else f"{int_part}.{frac_part}"
    text = format(Decimal(text), "f")
    return f"-{text}" if negative else text


def format_result(value: Fraction) -> str:
    """按 ROUND_HALF_EVEN 保留最多 12 位小数并去掉末尾零。

    用整数运算精确实现 ROUND_HALF_EVEN（与 Decimal.quantize 的
    ROUND_HALF_EVEN 语义一致），不依赖上下文精度，最后经 Decimal
    规范化输出。
    """
    num, den = value.numerator, value.denominator
    q, r = divmod(abs(num) * 10 ** MAX_DECIMALS, den)
    twice = r * 2
    if twice > den or (twice == den and q % 2 == 1):
        q += 1
    return _format_scaled(-q if num < 0 else q)


def format_uncertainty(variance: Fraction) -> str:
    """把非负方差开方后按 ROUND_HALF_EVEN 保留最多 12 位小数并去掉末尾零。

    全程整数算术：round(√(n/d) · 10^12) = round_half_even(√(n·10^24/d))。
    记 scaled = n·10^24、root = ⌊√(scaled/d)⌋，则真实平方根超过
    root + 1/2 当且仅当 4·scaled > (2·root+1)²·d；相等时恰为平局，
    按 ROUND_HALF_EVEN 向偶数进位。
    """
    if variance < 0:
        raise ValueError("variance must be non-negative")
    n, d = variance.numerator, variance.denominator
    scaled = n * 10 ** (2 * MAX_DECIMALS)
    q, r = divmod(scaled, d)
    root = isqrt(q)
    if r == 0 and root * root == q:
        return _format_scaled(root)  # 完全平方，无舍入
    lhs = 4 * scaled
    rhs = (2 * root + 1) ** 2 * d
    if lhs > rhs or (lhs == rhs and root % 2 == 1):
        root += 1
    return _format_scaled(root)
