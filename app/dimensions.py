"""由多条乘积等式推断未知变量的四维量纲。

每条等式写成左右两侧的因子数组（因子为带幂次的变量或单位），例如
``F = m*a`` 记作 ``[F] = [m][a]``。把所有因子的幂次合并后，每条等式在
（长度、质量、时间、温度）四维上各给出一个线性方程，跨等式组成线性方程组：

    A · x = b

矩阵元全部为有理数（``Fraction``）。仅当方程组对四个维度都相容、系数矩阵
列满秩（每个变量的每个维度指数都唯一）且唯一解恰为整数时，才接受结果；
否则分别归类为约束矛盾、欠定或唯一解含分数指数。

单位因子复用 :mod:`app.converter` 的单位表达式解析器，但只取量纲向量；
``°C`` / ``°F`` 是偏移温标，没有唯一的量纲语义，这里一律拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .converter import (
    DIM_NAMES,
    OffsetScaleInCompoundError,
    Quantity,
    UnitSyntaxError,
    parse_expression,
)


# ---------------------------------------------------------------------------
# 输入结构与错误类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FactorInput:
    """一个因子：var/unit 恰有一个非空，power ∈ [-6, 6]。"""
    var: str | None = None
    unit: str | None = None
    power: int = 1


@dataclass(frozen=True)
class EquationInput:
    left: tuple[FactorInput, ...]
    right: tuple[FactorInput, ...]


class DimensionInferenceError(Exception):
    """推断失败的基类；error 为对外的错误类型标识。"""

    error = "inference_error"

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class NoVariableError(DimensionInferenceError):
    error = "no_variable"


class InconsistentConstraintsError(DimensionInferenceError):
    error = "inconsistent_constraints"


class UnderdeterminedError(DimensionInferenceError):
    error = "underdetermined"


class FractionalSolutionError(DimensionInferenceError):
    error = "fractional_dimensions"


# ---------------------------------------------------------------------------
# 单位因子：复用解析器，只取量纲，拒绝偏移温标
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnitFactorError(Exception):
    """单位表达式无法用于量纲推断；携带因子坐标与解析位置。"""
    message: str
    kind: str  # "syntax_error" | "offset_temperature_not_allowed"
    position: int | None
    equation: int = 0
    side: str = "left"  # "left" | "right"
    factor: int = 0


def parse_unit_dimensions(expr: str) -> tuple[int, int, int, int]:
    """解析单位表达式并返回其四维量纲；拒绝 °C / °F。"""
    try:
        quantity: Quantity = parse_expression(expr)
    except UnitSyntaxError as exc:
        raise UnitFactorError(exc.message, "syntax_error", exc.position)
    except OffsetScaleInCompoundError as exc:
        raise UnitFactorError(
            "offset temperature scales °C/°F are not allowed; use K",
            "offset_temperature_not_allowed", exc.position)
    if quantity.offset_unit is not None:
        raise UnitFactorError(
            f"offset temperature scale {quantity.offset_unit!r} is not allowed; "
            "use K", "offset_temperature_not_allowed", quantity.pos)
    return quantity.dims


# ---------------------------------------------------------------------------
# 有理系数线性方程组（稀疏 Gauss-Jordan 消元）
# ---------------------------------------------------------------------------

# 增广行：{列索引: Fraction}，列 n_vars 为 RHS；恒等式 0=0 为空行
Row = dict[int, Fraction]


def _rref(rows: list[Row], n_vars: int) -> tuple[dict[int, Row], bool]:
    """对增广矩阵 [A | b] 做 Gauss-Jordan 消元。

    返回 (系数主元行 {列: 行}, 是否矛盾)。矛盾指系数部分全零而增广列非零，
    即 0 = 非零。
    """
    pivots: dict[int, Row] = {}
    # 主元列从左到右（含可能落在增广列的矛盾主元）
    n_cols = n_vars + 1
    for col in range(n_cols):
        pivot = None
        for r in rows:
            if r.get(col, 0) == 0:
                continue
            # 该行在更靠左的主元列上必须已被消净，否则它是某主元行
            if all(r.get(pc, 0) == 0 for pc in pivots):
                pivot = r
                break
        if pivot is None:
            continue
        factor = pivot[col]
        for c in list(pivot):
            pivot[c] /= factor
        for r in rows:
            if r is pivot or r.get(col, 0) == 0:
                continue
            k = r[col]
            for c, v in list(pivot.items()):
                nv = r.get(c, 0) - k * v
                if nv:
                    r[c] = nv
                else:
                    r.pop(c, None)
        pivots[col] = pivot

    inconsistent = any(
        all(r.get(c, 0) == 0 for c in range(n_vars)) and r.get(n_vars, 0) != 0
        for r in rows)
    coeff_pivots = {c: r for c, r in pivots.items() if c < n_vars}
    return coeff_pivots, inconsistent


def _solve(coeff_rows: list[dict[int, Fraction]],
           rhs_by_dim: list[list[int]], n_vars: int,
           variables: list[str]) -> dict[str, tuple[int, int, int, int]]:
    """逐维解 A·x=b，按矛盾 > 欠定 > 分数解的优先级报错。

    系数矩阵对四个维度完全相同，只有 RHS 不同；先逐维消元以优先发现任意
    维度上的矛盾（秩亏缺与矛盾可能同时出现），再判断自由变量与分数解。
    """
    dim_pivots: list[dict[int, Row]] = []

    for dim in range(4):
        rows: list[Row] = []
        for row, b in zip(coeff_rows, rhs_by_dim[dim]):
            full = {c: Fraction(v) for c, v in row.items()}
            if b != 0:
                full[n_vars] = Fraction(b)
            rows.append(full)

        pivots, inconsistent = _rref(rows, n_vars)
        if inconsistent:
            raise InconsistentConstraintsError(
                f"constraints are inconsistent in the {DIM_NAMES[dim]} dimension",
                dimension=DIM_NAMES[dim])
        dim_pivots.append(pivots)

    # 系数矩阵列秩与维度无关，用第一维的主元判定自由变量
    free_cols = [c for c in range(n_vars) if c not in dim_pivots[0]]
    if free_cols:
        free_variables = sorted(variables[c] for c in free_cols)
        raise UnderdeterminedError(
            "constraints are underdetermined; "
            f"{len(free_variables)} variable(s) lack a unique solution: "
            + ", ".join(free_variables),
            free_variables=free_variables)

    solutions: dict[int, list[Fraction]] = {c: [] for c in range(n_vars)}
    for dim in range(4):
        pivots = dim_pivots[dim]
        for c in range(n_vars):
            solutions[c].append(pivots[c].get(n_vars, Fraction(0)))

    fractional = sorted(
        variables[c] for c in range(n_vars)
        if any(v.denominator != 1 for v in solutions[c]))
    if fractional:
        raise FractionalSolutionError(
            "the unique solution contains fractional exponents for: "
            + ", ".join(fractional),
            variables=fractional)

    return {
        name: tuple(int(solutions[c][d]) for d in range(4))
        for c, name in enumerate(variables)
    }


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def infer_dimensions(
        equations: list[EquationInput]
) -> dict[str, tuple[int, int, int, int]]:
    """合并因子幂次构建跨等式线性约束并求解四维量纲。

    成功返回 {变量名: (length, mass, time, temperature) 整数指数}；
    失败抛出 DimensionInferenceError 子类；单位因子非法时抛出 UnitFactorError。
    """
    variables: list[str] = []
    index: dict[str, int] = {}

    coeff_rows: list[dict[int, Fraction]] = []
    rhs_by_dim: list[list[int]] = [[], [], [], []]
    unit_cache: dict[str, tuple[int, int, int, int]] = {}

    def var_index(name: str) -> int:
        if name not in index:
            index[name] = len(variables)
            variables.append(name)
        return index[name]

    for eq_index, equation in enumerate(equations):
        row: dict[int, Fraction] = {}
        rhs = [0, 0, 0, 0]

        def consume(factors, sign: int, side: str) -> None:
            # 左侧取 +、右侧取 -：sum(left) - sum(right) = 0，即 A·x = -单位项
            for factor_index, factor in enumerate(factors):
                p = sign * factor.power
                if factor.var is not None:
                    col = var_index(factor.var)
                    row[col] = row.get(col, Fraction(0)) + p
                else:
                    assert factor.unit is not None
                    if factor.unit not in unit_cache:
                        try:
                            unit_cache[factor.unit] = parse_unit_dimensions(
                                factor.unit)
                        except UnitFactorError as exc:
                            raise UnitFactorError(
                                exc.message, exc.kind, exc.position,
                                eq_index, side, factor_index)
                    dims = unit_cache[factor.unit]
                    for d in range(4):
                        rhs[d] -= p * dims[d]

        consume(equation.left, +1, "left")
        consume(equation.right, -1, "right")
        coeff_rows.append({c: v for c, v in row.items() if v != 0})
        for d in range(4):
            rhs_by_dim[d].append(rhs[d])

    if not variables:
        raise NoVariableError(
            'at least one variable factor ("var") is required')

    return _solve(coeff_rows, rhs_by_dim, len(variables), variables)
