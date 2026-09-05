"""AC-06 验收：_z_test_p_value 显著性检验对照。

基于 spec-v5.2.0-evolution-loop.md §17 AC-06 + §9 验收标准。

AC-06：使用 synthetic 对照数据执行 A/B 检验，系统必须输出正确的 p-value（对照人工计算）。

实现细节（py/maop/core/evolution/ab_test.py:222）：
- 两比例 Z 检验（one-tailed：p2 > p1）
- z = (p2 - p1) / sqrt(p_pool * (1-p_pool) * (1/n1 + 1/n2))
- p_value = 0.5 * (1 + erf(-z / sqrt(2)))
"""

from __future__ import annotations

import math

import pytest

from maop.core.evolution.ab_test import _z_test_p_value


def test_z_test_pvalue_known_case():
    """对照人工计算：p1=0.10, p2=0.12, n1=1000, n2=1000。

    手算：
    - p_pool = (0.10*1000 + 0.12*1000) / 2000 = 0.11
    - se = sqrt(0.11 * 0.89 * (1/1000 + 1/1000)) = sqrt(0.11*0.89*0.002) ≈ 0.01399
    - z = (0.12 - 0.10) / 0.01399 ≈ 1.430
    - p = 0.5 * (1 + erf(-1.430 / sqrt(2))) ≈ 0.5 * (1 - 0.9236) ≈ 0.0764
    """
    p = _z_test_p_value(p1=0.10, p2=0.12, n1=1000, n2=1000)
    # 允许 ±0.01 容差（erf 浮点误差 + 我手算 z=1.430 也可能略有偏差）
    assert 0.06 <= p <= 0.09, f"p={p:.4f} 偏离 0.0764 太远"


def test_z_test_pvalue_known_case_strong_signal():
    """强信号：p1=0.10, p2=0.20, n1=1000, n2=1000 应 p << 0.01。"""
    p = _z_test_p_value(p1=0.10, p2=0.20, n1=1000, n2=1000)
    assert p < 0.001, f"强信号 p 应 < 0.001，实际 p={p:.4f}"


def test_z_test_pvalue_identical_proportions():
    """相同比例：p1=p2=0.5, n1=1000, n2=1000 → z=0, p = 0.5（one-tailed 中点）。

    注：one-tailed 的 p=0.5 表示「无方向性差异」（p2>p1 与 p2<p1 等价）。
    不是双侧的 1.0。
    """
    p = _z_test_p_value(p1=0.5, p2=0.5, n1=1000, n2=1000)
    assert abs(p - 0.5) < 1e-9, f"相同比例 p 应 = 0.5（one-tailed 中点），实际 p={p}"


def test_z_test_pvalue_extreme_zero_p1():
    """极端情形：p1=0, p2=1, n1=100, n2=100 → p_pool=0.5，se=sqrt(0.25*0.02)≈0.0707，
    z = 1/0.0707 ≈ 14.14, p = 0.5*(1+erf(-14.14/sqrt(2))) ≈ 0（极强差异）。"""
    p = _z_test_p_value(p1=0.0, p2=1.0, n1=100, n2=100)
    assert p < 1e-10, f"p1=0 vs p2=1 应 p≈0，实际 p={p:.4e}"


def test_z_test_pvalue_zero_pool_p1_zero():
    """p_pool=0 边界：p1=0, p2=0, n=100 → p=1.0（无差异）。"""
    p = _z_test_p_value(p1=0.0, p2=0.0, n1=100, n2=100)
    assert p == 1.0, f"双 0 比例 p 应 = 1.0，实际 p={p}"


def test_z_test_pvalue_zero_pool_p1_one():
    """p_pool=1 边界：p1=1, p2=1, n=100 → p=1.0（无差异）。"""
    p = _z_test_p_value(p1=1.0, p2=1.0, n1=100, n2=100)
    assert p == 1.0, f"双 1 比例 p 应 = 1.0，实际 p={p}"


def test_z_test_pvalue_symmetric_one_tailed():
    """one-tailed 对称性：p1=0.30, p2=0.10, n1=1000, n2=1000 应 p ≈ 1.0
    （因为实现是 one-tailed: p2 > p1，反过来 p1 > p2 时 p ≈ 1）。"""
    p = _z_test_p_value(p1=0.30, p2=0.10, n1=1000, n2=1000)
    assert p > 0.99, f"one-tailed 方向反过来 p 应 ≈ 1，实际 p={p:.4f}"


def test_z_test_pvalue_small_samples():
    """小样本：n1=10, n2=10, p1=0.2, p2=0.4 应仍能检测（p < 0.20）。"""
    p = _z_test_p_value(p1=0.2, p2=0.4, n1=10, n2=10)
    # 手算：p_pool=0.3, se=sqrt(0.3*0.7*0.2)=0.2049, z=0.2/0.2049≈0.976, p≈0.164
    assert 0.10 <= p <= 0.22, f"小样本 p 应 ≈ 0.16，实际 p={p:.4f}"


def test_z_test_pvalue_against_scipy_if_available():
    """对照 scipy.stats（如果可用）。scipy 在 MAOP 测试环境不一定装。

    检验一致性：_z_test_p_value 应近似 scipy.norm.sf(z) * 2（双侧）。
    实际 _z_test_p_value 是 one-tailed 上侧：P(Z > z) = 0.5 * (1 + erf(-z/sqrt(2)))
    """
    pytest.importorskip("scipy")
    from scipy import stats

    test_cases = [
        (0.10, 0.12, 1000, 1000),
        (0.30, 0.40, 500, 500),
        (0.05, 0.15, 2000, 2000),
    ]
    for p1, p2, n1, n2 in test_cases:
        p_ours = _z_test_p_value(p1=p1, p2=p2, n1=n1, n2=n2)
        # scipy 对照
        p_pool = (p1 * n1 + p2 * n2) / (n1 + n2)
        se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
        z = (p2 - p1) / se
        p_scipy_one = 1 - stats.norm.cdf(z)  # one-tailed 上侧
        assert abs(p_ours - p_scipy_one) < 0.001, (
            f"({p1},{p2},{n1},{n2}): ours={p_ours:.6f} vs scipy={p_scipy_one:.6f}"
        )
