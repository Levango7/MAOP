"""plan_quality subpackage — F2-04 plan quality learning.

Split from plan_quality.py into a subpackage.  Import from ``plan_quality``
directly; do not import submodules directly.
"""

from maop.core.memory.plan_quality.evaluator import PlanQualityEvaluator
from maop.core.memory.plan_quality.features import (
    PlanFeatureExtractor,
    PlanQualityModel,
    _bce,
    _sigmoid,
    compute_quality_label,
)
from maop.core.memory.plan_quality.pipeline import (
    PlanQualityDataPipeline,
    _SyntheticPlan,
)
from maop.core.memory.plan_quality.versioning import (
    PlanVersion,
    PlanVersionManager,
)

__all__ = [
    "PlanFeatureExtractor",
    "PlanQualityDataPipeline",
    "PlanQualityEvaluator",
    "PlanQualityModel",
    "PlanVersion",
    "PlanVersionManager",
    "_SyntheticPlan",
    "_bce",
    "_sigmoid",
    "compute_quality_label",
]
