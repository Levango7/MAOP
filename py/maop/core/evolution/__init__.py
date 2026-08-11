"""Evolution subpackage.

自演化、A/B 测试、回归、版本演化。

Modules:
    evolution_loop, evolution_loop_types, evolution_strategies, ab_test,
    regression, prompt_version, skill_version
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "logger",
    "ExperimentConfig",
    "VariantStats",
    "EvaluationResult",
    "ABTestManager",
    "logger",
    "EvolutionLoop",
    "LoopPhase",
    "PhaseResult",
    "EvolutionSuggestion",
    "LoopReport",
    "logger",
    "EvolutionDecision",
    "StrategyConfig",
    "BaseStrategy",
    "ConservativeStrategy",
    "AggressiveStrategy",
    "BalancedStrategy",
    "CostAwareStrategy",
    "STRATEGY_MAP",
    "StrategyEngine",
    "logger",
    "PromptVersion",
    "PromptVersionManager",
    "logger",
    "TestCase",
    "TestResult",
    "RegressionReport",
    "PersonaConfig",
    "SimulationTurn",
    "SimulationResult",
    "RegressionTestRunner",
    "PersonaSimulator",
    "logger",
    "SkillStep",
    "SkillMeta",
    "SkillStepResult",
    "SkillExecutionResult",
    "SkillVersionManager",
]

# 符号 → 子模块名映射（惰性加载用，含私有符号）
_SYMBOL_TO_MODULE: dict[str, str] = {
    # 注: 多个子模块均导出同名符号（如 logger），
    # 按字典构造语义仅最后一个映射生效，与重构前运行时行为一致。
    "ExperimentConfig": "ab_test",
    "VariantStats": "ab_test",
    "EvaluationResult": "ab_test",
    "_AB_TEST_DDL": "ab_test",
    "ABTestManager": "ab_test",
    "_z_test_p_value": "ab_test",
    "_EVOLUTION_LOOP_DDL": "evolution_loop",
    "EvolutionLoop": "evolution_loop",
    "LoopPhase": "evolution_loop_types",
    "PhaseResult": "evolution_loop_types",
    "EvolutionSuggestion": "evolution_loop_types",
    "LoopReport": "evolution_loop_types",
    "EvolutionDecision": "evolution_strategies",
    "StrategyConfig": "evolution_strategies",
    "BaseStrategy": "evolution_strategies",
    "ConservativeStrategy": "evolution_strategies",
    "AggressiveStrategy": "evolution_strategies",
    "BalancedStrategy": "evolution_strategies",
    "CostAwareStrategy": "evolution_strategies",
    "STRATEGY_MAP": "evolution_strategies",
    "StrategyEngine": "evolution_strategies",
    "PromptVersion": "prompt_version",
    "_PROMPT_VER_DDL": "prompt_version",
    "PromptVersionManager": "prompt_version",
    "TestCase": "regression",
    "TestResult": "regression",
    "RegressionReport": "regression",
    "PersonaConfig": "regression",
    "SimulationTurn": "regression",
    "SimulationResult": "regression",
    "RegressionTestRunner": "regression",
    "PersonaSimulator": "regression",
    "logger": "skill_version",
    "SkillStep": "skill_version",
    "SkillMeta": "skill_version",
    "SkillStepResult": "skill_version",
    "SkillExecutionResult": "skill_version",
    "SkillVersionManager": "skill_version",
}


def __getattr__(name: str) -> Any:
    """惰性加载子模块符号，避免循环导入。"""
    if name in _SYMBOL_TO_MODULE:
        mod_name = _SYMBOL_TO_MODULE[name]
        mod = importlib.import_module(f".{mod_name}", __name__)
        value = getattr(mod, name)
        globals()[name] = value  # 缓存，下次直接访问
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
