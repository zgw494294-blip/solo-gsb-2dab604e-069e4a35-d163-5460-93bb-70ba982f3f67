"""最简无量纲乘积搜索的核心逻辑测试（不经过 HTTP）。"""

from math import gcd
from random import Random

import pytest

from app.dimensionless import (
    MAX_EXPONENT,
    NoDimensionlessGroupError,
    find_dimensionless_group,
)


def dims(length=0, mass=0, time=0, temperature=0):
    return (length, mass, time, temperature)


def brute_force(dim_vectors):
    """暴力枚举 [-6, 6]^n 的全部组合，返回与 find_dimensionless_group
    相同规则下的最优解（无解压返回 None）。用于小 n 的对拍。"""
    best = None

    def walk(index, current):
        nonlocal best
        if index == len(dim_vectors):
            if not any(current):
                return
            for e in current:
                if e:
                    if e < 0:
                        return
                    break
            if any(sum(e * v[d] for e, v in zip(current, dim_vectors))
                   for d in range(4)):
                return
            cand = (sum(abs(e) for e in current), tuple(current))
            if best is None or cand < best:
                best = cand
            return
        for e in range(-MAX_EXPONENT, MAX_EXPONENT + 1):
            walk(index + 1, current + [e])

    walk(0, [])
    return None if best is None else best[1]


# ---------------------------------------------------------------- 基本情形

def test_single_dimensionless_quantity():
    assert find_dimensionless_group([dims()]) == (1,)


def test_single_dimensional_quantity_has_no_solution():
    with pytest.raises(NoDimensionlessGroupError):
        find_dimensionless_group([dims(length=1)])


def test_independent_dimensions_have_no_solution():
    with pytest.raises(NoDimensionlessGroupError):
        find_dimensionless_group([dims(length=1), dims(time=1)])


def test_two_identical_dimensions():
    assert find_dimensionless_group([dims(length=1), dims(length=1)]) == (1, -1)


# ---------------------------------------------------------------- 规则细节

def test_result_is_primitive():
    # m^2 与 m^4：最简解为 (2, -1)，而不是 (4, -2) 等倍数解
    result = find_dimensionless_group([dims(length=2), dims(length=4)])
    assert result == (2, -1)
    nonzero = [e for e in result if e]
    assert gcd(*map(abs, nonzero)) == 1


def test_sign_convention_picks_positive_first_nonzero():
    # force = mass * accel：(-1, 1, -1) 首个非零为负，取其相反数
    result = find_dimensionless_group(
        [dims(length=1, time=-2),      # accel
         dims(length=1, mass=1, time=-2),  # force
         dims(mass=1)])                # mass
    assert result == (1, -1, 1)


def test_lexicographic_tie_break():
    # 三个相同量纲：L1 最小的可行解不唯一，取字典序最小的 (0, 1, -1)
    assert find_dimensionless_group([dims(length=1)] * 3) == (0, 1, -1)


def test_exponent_at_boundary():
    assert find_dimensionless_group(
        [dims(length=1), dims(length=6)]) == (6, -1)


def test_exponent_range_exceeded():
    with pytest.raises(NoDimensionlessGroupError):
        find_dimensionless_group([dims(length=1), dims(length=7)])


def test_temperature_dimension():
    assert find_dimensionless_group(
        [dims(temperature=1), dims(temperature=1)]) == (1, -1)


# ---------------------------------------------------------------- 与暴力枚举对拍

def test_against_brute_force_on_random_instances():
    rng = Random(20260926)
    for _ in range(300):
        n = rng.randint(1, 5)
        vectors = [dims(*[rng.randint(-3, 3) for _ in range(4)])
                   for _ in range(n)]
        expected = brute_force(vectors)
        if expected is None:
            with pytest.raises(NoDimensionlessGroupError):
                find_dimensionless_group(vectors)
        else:
            assert find_dimensionless_group(vectors) == expected
