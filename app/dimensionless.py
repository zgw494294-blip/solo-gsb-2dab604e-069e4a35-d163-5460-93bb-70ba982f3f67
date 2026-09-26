"""从 1～8 个具名量的四维量纲中搜索最简无量纲乘积。

每个量的单位表达式解析为（长度、质量、时间、温度）四维量纲向量后，寻找一组
不全为零、每项 ∈ [-6, 6] 的整数指数，使四个维度的加权和均为零。可行解中先取
指数绝对值之和最小者，再按（按名称排序的）指数元组字典序取唯一结果。

搜索用中途相遇（meet-in-the-middle）：把按名称排序的变量分成两半，各自枚举
[-6, 6]^k 的全部指数组合并记录加权和向量，再双向配对，使两侧向量互为相反数。
n ≤ 8 时每半侧最多 13^4 = 28561 种组合，枚举量可控。

两个化简事实：

- 若绝对值之和最小的可行解的非零指数最大公约数 g > 1，则整体除以 g 仍是
  可行解（符号约定不变、各项仍在 ±6 内）且绝对值之和更小，矛盾；故最优解
  自动满足 gcd = 1，无需额外过滤。
- 被查找的一侧只需保留每个加权和向量上 (绝对值之和, 指数元组) 最小者：固定
  另一侧后，它支配同向量的所有元组。被枚举的一侧则遍历全部组合并双向配对，
  因此只在一侧出现的非平凡解、以及符号约定只由一侧决定的边界情形都能覆盖。
"""

from __future__ import annotations

MAX_EXPONENT = 6


class NoDimensionlessGroupError(Exception):
    """在 ±6 指数范围内不存在非平凡的无量纲组合。"""


def _enumerate_combos(dims: list[tuple[int, int, int, int]]):
    """枚举半侧变量集合的全部指数组合：[(加权和向量, 指数元组)]。"""
    combos = [((0, 0, 0, 0), ())]
    for vec in dims:
        combos = [
            ((base[0] + e * vec[0], base[1] + e * vec[1],
              base[2] + e * vec[2], base[3] + e * vec[3]),
             exponents + (e,))
            for base, exponents in combos
            for e in range(-MAX_EXPONENT, MAX_EXPONENT + 1)
        ]
    return combos


def _best_by_vector(combos) -> dict:
    """每个加权和向量只保留 (绝对值之和, 指数元组) 最小者。"""
    best = {}
    for vec, exponents in combos:
        cand = (sum(abs(e) for e in exponents), exponents)
        if vec not in best or cand < best[vec]:
            best[vec] = cand
    return best


def find_dimensionless_group(dims: list[tuple[int, int, int, int]]):
    """求按名称排序的四维量纲向量序列（1～8 个）的最简无量纲指数组合。

    返回与输入同序的指数元组（含零指数）；无可行解时抛出
    NoDimensionlessGroupError。
    """
    mid = len(dims) // 2
    left_combos = _enumerate_combos(dims[:mid])
    right_combos = _enumerate_combos(dims[mid:])
    left_best = _best_by_vector(left_combos)
    right_best = _best_by_vector(right_combos)

    best = None  # (绝对值之和, 指数元组)

    def consider(exponents) -> None:
        nonlocal best
        for e in exponents:
            if e:
                if e < 0:
                    return  # 首个非零指数为负，违反符号约定
                break
        else:
            return  # 全零组合
        cand = (sum(abs(e) for e in exponents), exponents)
        if best is None or cand < best:
            best = cand

    # 双向配对：一侧全量枚举，另一侧按相反向量查最优
    for vec, exponents in left_combos:
        match = right_best.get(tuple(-x for x in vec))
        if match is not None:
            consider(exponents + match[1])
    for vec, exponents in right_combos:
        match = left_best.get(tuple(-x for x in vec))
        if match is not None:
            consider(match[1] + exponents)

    if best is None:
        raise NoDimensionlessGroupError(
            "no dimensionless product exists with exponents in "
            f"-{MAX_EXPONENT}..{MAX_EXPONENT}")
    return best[1]
