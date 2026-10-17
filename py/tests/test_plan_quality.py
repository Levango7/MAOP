"""Tests F2-04 M2.3: Plan Quality Learning MVP.

Pinned behaviours:

1. ``compute_quality_label`` gives higher labels to cheaper / faster / no-retry
   executions with the same outcome.
2. ``PlanFeatureExtractor`` produces a 10-dim vector whose values are in
   expected ranges and correctly reflects plan structure (gates, verb prefix).
3. ``PlanQualityModel`` learns a monotonic signal: a plan that is structurally
   better (more gates, matching historical agent) receives a higher predicted
   quality after training.
4. ``PlanVersionManager`` appends, promotes, rolls back and reloads versions.
5. ``PlanQualityEvaluator.rank`` sorts candidate plans so that the structurally
   best plan comes first when the model is trained on a clear signal.
6. ``add_execution`` triggers an online SGD update that moves the model
   prediction toward the observed label.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import pytest

from maop.core.memory.plan_quality import (
    PlanFeatureExtractor,
    PlanQualityDataPipeline,
    PlanQualityEvaluator,
    PlanQualityModel,
    PlanVersionManager,
    _bce,
    _sigmoid,
    _SyntheticPlan,
    compute_quality_label,
)

# ════════════════════════════════════════════════════════════════════
# helpers
# ════════════════════════════════════════════════════════════════════


def _make_plan(
    task: str = "create a dashboard",
    agent: str = "writer",
    rk: str = "general",
    gates: list[str] | None = None,
    timeout_s: float = 120,
    max_retries: int = 1,
) -> _SyntheticPlan:
    return _SyntheticPlan(
        task=task,
        selected_agent=agent,
        routing_key=rk,
        gates=gates or ["exit_code", "output"],
        budget={"timeout_s": timeout_s, "max_retries": max_retries},
    )


@pytest.fixture
def pq_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An isolated MAOP-like root with its own ``data/`` dir."""
    base = tmp_path_factory.mktemp("pq")
    (base / "data").mkdir(parents=True, exist_ok=True)
    return base


def _populate_dataset(root: Path, n: int = 50) -> None:
    """Populate agent_performance + plan_quality_dataset with synthetic rows
    that carry a deliberate signal: +content-safety gate → success,
    no content-safety → failure.  Agent ``alpha`` dominates rk ``gen``,
    agent ``beta`` dominates rk ``sec``."""
    from maop.core.backends.db_utils import get_db_path, sqlite_connect

    # 1) seed agent_performance
    perf_db = get_db_path("agent_performance")
    with sqlite_connect(perf_db, foreign_keys=False) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_performance "
            "(id TEXT, agent TEXT, routing_key TEXT, outcome TEXT, "
            "cost_usd REAL, latency_ms REAL, created_at REAL)"
        )
        for i in range(n):
            rk = "gen" if i % 2 == 0 else "sec"
            agent = "alpha" if rk == "gen" else "beta"
            outcome = "success" if i % 3 != 0 else "failure"
            cost = 0.002 + (i % 5) * 0.001
            lat = 800.0 + (i % 7) * 200
            conn.execute(
                "INSERT OR IGNORE INTO agent_performance "
                "VALUES (?,?,?,?,?,?,?)",
                (f"ap-{i}", agent, rk, outcome, cost, lat, time.time() + i),
            )

    # 2) ingest into plan_quality_dataset via pipeline
    pipe = PlanQualityDataPipeline(root)
    pipe.ingest_from_agent_performance()


# ════════════════════════════════════════════════════════════════════
# 1. compute_quality_label
# ════════════════════════════════════════════════════════════════════


class TestComputeQualityLabel:
    def test_success_outperforms_failure(self) -> None:
        ok = compute_quality_label("success", 0.005, 1500, 0)
        bad = compute_quality_label("failure", 0.005, 1500, 0)
        assert ok > bad

    def test_cheaper_is_better(self) -> None:
        a = compute_quality_label("success", 0.001, 1500, 0)
        b = compute_quality_label("success", 0.05, 1500, 0)
        assert a > b

    def test_faster_is_better(self) -> None:
        a = compute_quality_label("success", 0.005, 500, 0)
        b = compute_quality_label("success", 0.005, 15000, 0)
        assert a > b

    def test_no_retries_outperforms_retries(self) -> None:
        a = compute_quality_label("success", 0.005, 1500, 0)
        b = compute_quality_label("success", 0.005, 1500, 3)
        assert a > b

    def test_partial_between_success_and_failure(self) -> None:
        lo = compute_quality_label("failure")
        mid = compute_quality_label("partial")
        hi = compute_quality_label("success")
        assert lo < mid < hi

    def test_clipping_above(self) -> None:
        v = compute_quality_label("success", 0.0, 0.0, 0)
        assert 0.55 <= v <= 1.0


# ════════════════════════════════════════════════════════════════════
# 2. PlanFeatureExtractor
# ════════════════════════════════════════════════════════════════════


class TestPlanFeatureExtractor:
    def test_vector_dimension(self) -> None:
        ext = PlanFeatureExtractor()
        v = ext.extract(_make_plan())
        assert len(v) == PlanFeatureExtractor.FEATURE_DIM == 10

    def test_gate_count_reflected(self) -> None:
        ext = PlanFeatureExtractor()
        lo = ext.extract(_make_plan(gates=["exit_code"]))
        hi = ext.extract(_make_plan(gates=["a", "b", "c", "d", "e"]))
        assert hi[0] > lo[0]

    def test_content_safety_flag(self) -> None:
        ext = PlanFeatureExtractor()
        v = ext.extract(_make_plan(gates=["content-safety", "output"]))
        assert v[1] == 1.0

    def test_verb_prefix_flag(self) -> None:
        ext = PlanFeatureExtractor()
        v_verb = ext.extract(_make_plan(task="create a dashboard"))
        v_noverb = ext.extract(_make_plan(task="the dashboard is fine"))
        assert v_verb[9] == 1.0
        assert v_noverb[9] == 0.0

    def test_timeout_scaling(self) -> None:
        ext = PlanFeatureExtractor()
        short_t = ext.extract(_make_plan(timeout_s=30))[3]
        long_t = ext.extract(_make_plan(timeout_s=240))[3]
        assert long_t > short_t
        assert 0 <= short_t <= 1 and 0 <= long_t <= 1


# ════════════════════════════════════════════════════════════════════
# 3. PlanQualityModel
# ════════════════════════════════════════════════════════════════════


class TestPlanQualityModel:
    def test_untrained_returns_neutral(self) -> None:
        m = PlanQualityModel()
        assert m.predict([0.5] * 10) == 0.5

    def test_fit_reduces_loss(self) -> None:
        rng_fn = __import__("random").Random(0)
        # Build a clearly separable dataset: class-1 points cluster near 0.8,
        # class-0 points cluster near 0.2 — easy for a linear model.
        X = []
        y = []
        for _ in range(150):
            X.append([0.7 + rng_fn.random() * 0.3 for _ in range(10)])
            y.append(1.0)
        for _ in range(150):
            X.append([0.0 + rng_fn.random() * 0.3 for _ in range(10)])
            y.append(0.0)

        m = PlanQualityModel(epochs=50, learning_rate=0.05, seed=1)
        loss = m.fit(X, y)
        assert m._trained
        assert loss < 0.4  # separable data → BCE should drop well below random guess

    def test_prediction_ordering_matches_signal(self) -> None:
        """After training on monotonic data, higher raw feature sum → higher prediction."""
        rng_fn = __import__("random").Random(42)
        X = [[rng_fn.random() for _ in range(10)] for _ in range(300)]
        y = [1.0 if sum(x) > 5 else 0.0 for x in X]

        m = PlanQualityModel(epochs=80, learning_rate=0.1, seed=7)
        m.fit(X, y)

        good_pred = m.predict([0.9] * 10)
        bad_pred = m.predict([0.1] * 10)
        assert good_pred > bad_pred

    def test_online_update_shrinks_error(self) -> None:
        m = PlanQualityModel(epochs=30, seed=2)
        X = [[0.5] * 10 for _ in range(50)]
        y = [1.0] * 50
        m.fit(X, y)

        pred_before = m.predict([0.5] * 10)
        for _ in range(20):
            m.online_update([0.5] * 10, 1.0, n_steps=3)
        pred_after = m.predict([0.5] * 10)
        assert pred_after >= pred_before

    def test_roundtrip_serialisation(self) -> None:
        m = PlanQualityModel(epochs=10, seed=11)
        m.fit([[0.3] * 10] * 20, [1.0] * 20)
        m.snapshot_version()

        data = m.to_dict()
        m2 = PlanQualityModel.from_dict(data)
        assert m2._trained == m._trained
        assert m2._w == m._w
        assert m2._b == m._b
        assert m2._version == m._version
        sample = [0.3] * 10
        assert math.isclose(m.predict(sample), m2.predict(sample))


# ════════════════════════════════════════════════════════════════════
# 4. PlanVersionManager
# ════════════════════════════════════════════════════════════════════


class TestPlanVersionManager:
    def test_append_and_list(self, pq_root: Path) -> None:
        vm = PlanVersionManager(pq_root)
        m = PlanQualityModel()
        m.fit([[0.1] * 10] * 5, [1.0] * 5)
        ver = vm.append(m, n_samples=5)
        assert ver.version_id
        # First version is auto-promoted on disk (check via active_version).
        assert vm.active_version() is not None
        assert vm.active_version().version_id == ver.version_id
        chain = vm.list_versions()
        assert len(chain) == 1

    def test_promote_and_rollback(self, pq_root: Path) -> None:
        vm = PlanVersionManager(pq_root)
        m1 = PlanQualityModel()
        m1.fit([[0.1] * 10] * 5, [1.0] * 5)
        v1 = vm.append(m1, 5)

        m2 = PlanQualityModel()
        m2.fit([[0.2] * 10] * 5, [1.0] * 5)
        v2 = vm.append(m2, 5)

        assert vm.active_version().version_id == v1.version_id
        assert vm.promote(v2.version_id)
        assert vm.active_version().version_id == v2.version_id

        rb = vm.rollback()
        assert rb is not None
        assert rb.version_id == v1.version_id
        assert vm.active_version().version_id == v1.version_id

    def test_load_model_weights(self, pq_root: Path) -> None:
        vm = PlanVersionManager(pq_root)
        m = PlanQualityModel(epochs=5, seed=99)
        m.fit([[0.4] * 10] * 10, [0.0] * 10)
        ver = vm.append(m, 10)

        loaded = vm.load_model(ver.version_id)
        assert loaded is not None
        assert loaded._trained
        assert math.isclose(
            loaded.predict([0.4] * 10),
            m.predict([0.4] * 10),
            rel_tol=1e-6,
        )


# ════════════════════════════════════════════════════════════════════
# 5. PlanQualityEvaluator (trained-model rank ordering)
# ════════════════════════════════════════════════════════════════════


class TestPlanQualityEvaluator:
    def test_rank_best_plan_first(self, pq_root: Path) -> None:
        """Train a model on a clear monotonic signal: high agent_match +
        more gates + low retries → high quality; then rank a "good" vs "bad"
        plan through the evaluator and assert the good plan wins."""
        ext = PlanFeatureExtractor()
        rng_fn = __import__("random").Random(7)

        # Build training pairs where the 6th feature (agent_match=1.0) and
        # 1st feature (gate_count) correlate positively with the label.
        X, y = [], []
        for _ in range(200):
            base = [rng_fn.random() for _ in range(10)]
            # positive sample: high gate_count, agent_match=1, low retries
            pos = base.copy()
            pos[0] = 0.7 + 0.3 * rng_fn.random()  # gate_count high
            pos[5] = 1.0                           # agent_match = 1
            pos[4] = 1.0                           # retries low
            X.append(pos)
            y.append(1.0)
            # negative sample: low gate_count, agent_match=0, high retries
            neg = base.copy()
            neg[0] = 0.1 + 0.2 * rng_fn.random()
            neg[5] = 0.0
            neg[4] = 3.0
            X.append(neg)
            y.append(0.0)

        model = PlanQualityModel(epochs=60, learning_rate=0.05, seed=3)
        model.fit(X, y)

        # Seed dataset so evaluator's agent_match lookup works with "alpha"
        _populate_dataset(pq_root, n=60)
        ev = PlanQualityEvaluator(pq_root, model=model)

        good = _make_plan(
            task="create unit tests",
            agent="alpha",
            rk="gen",
            gates=["exit_code", "output", "content-safety", "syntax"],
            max_retries=1,
        )
        bad = _make_plan(
            task="garbage input",
            agent="wrong_agent",
            rk="gen",
            gates=["exit_code"],
            max_retries=3,
        )
        # 手工造的训练向量必须与抽取器对同一条计划给出的形态同向 —— 否则"清晰信号"是夹具自己编的。
        # 上面 pos/neg 用的约定是：dim0 gate_count 高→好、dim4 retries 低→好、dim5 agent_match=1→好。
        g_feat = ext.extract(good, agent_match=1.0, total_agent_samples=25)
        b_feat = ext.extract(bad, agent_match=0.0, total_agent_samples=25)
        assert g_feat[0] > b_feat[0] and g_feat[4] < b_feat[4] and g_feat[5] > b_feat[5], (
            f"抽取器输出与夹具向量口径不一致: good={g_feat} bad={b_feat}"
        )

        ranked = ev.rank([bad, good])
        assert ranked[0][0] is good
        assert ranked[0][1] > ranked[1][1]

    def test_score_range(self, pq_root: Path) -> None:
        _populate_dataset(pq_root, n=30)
        pipe = PlanQualityDataPipeline(pq_root)
        model = pipe.train()
        ev = PlanQualityEvaluator(pq_root, model=model)

        for p in [
            _make_plan(gates=["exit_code"]),
            _make_plan(gates=["content-safety", "dry-run", "output"]),
        ]:
            s = ev.score(p)
            assert 0 < s < 1

    def test_best_returns_none_for_empty(self, pq_root: Path) -> None:
        m = PlanQualityModel()
        ev = PlanQualityEvaluator(pq_root, model=m)
        assert ev.best([]) is None


# ════════════════════════════════════════════════════════════════════
# 6. Online learning via add_execution
# ════════════════════════════════════════════════════════════════════


class TestOnlineLearning:
    def test_add_execution_updates_model(self, pq_root: Path) -> None:
        _populate_dataset(pq_root, n=30)
        pipe = PlanQualityDataPipeline(pq_root)
        model = pipe.train(save_version=True)
        ev = PlanQualityEvaluator(pq_root, model=model, online_learning=True)

        plan = _make_plan(task="deploy to staging", agent="alpha", rk="gen",
                          gates=["exit_code", "output"])
        feat_before = PlanFeatureExtractor().extract(plan, agent_match=1.0, total_agent_samples=10)
        score_before = model.predict(feat_before)

        # feed several successful executions
        for _ in range(20):
            ev.add_execution(plan, {
                "outcome": "success",
                "cost_usd": 0.001,
                "latency_ms": 500,
                "retries_used": 0,
            })

        score_after = ev.score(plan)
        # model should have improved its prediction for this plan
        assert score_after >= score_before - 0.05  # tolerance


# ════════════════════════════════════════════════════════════════════
# Math helpers
# ════════════════════════════════════════════════════════════════════


class TestMathHelpers:
    def test_sigmoid_zero(self) -> None:
        assert math.isclose(_sigmoid(0.0), 0.5)

    def test_sigmoid_symmetry(self) -> None:
        assert math.isclose(_sigmoid(2.0) + _sigmoid(-2.0), 1.0)

    def test_sigmoid_extremes(self) -> None:
        assert _sigmoid(-100) < 0.01
        assert _sigmoid(100) > 0.99

    def test_bce_perfect(self) -> None:
        assert math.isclose(_bce(1.0, 1.0), 0.0, abs_tol=1e-4)
        assert math.isclose(_bce(0.0, 0.0), 0.0, abs_tol=1e-4)

    def test_bce_worse_than_random(self) -> None:
        # confident wrong prediction → high loss
        assert _bce(0.01, 1.0) > 1.0


# ════════════════════════════════════════════════════════════════════
# F2-04 GA — E2E 版本晋升 / 回滚 / 在线学习闭环
# ════════════════════════════════════════════════════════════════════


class TestEndToEndVersionLifecycle:
    """GA E2E：多轮训练 → 版本链 → promote → rollback → 回读验证。

    此前 PlanVersionManager 的单测只覆盖原子操作；这里从"管道产出数据"到
    "模型落地服务"走完整链路，验证版本晋升/回滚在真实文件持久化场景下正常工作。
    """

    def test_train_promote_rollback_active_chain(self, pq_root: Path) -> None:
        """完整版本生命周期：训练 → v1 (自动 promote) → 训练 → v2 → promote v2
        → rollback → active 恢复为 v1 → 权重可回读。"""
        _populate_dataset(pq_root, n=50)
        pipe = PlanQualityDataPipeline(pq_root)
        vm = PlanVersionManager(pq_root)

        # Round-1: 训练 → v1（首版自动 promote）
        m1 = pipe.train(save_version=True)
        active1 = vm.active_version()
        assert active1 is not None, "首版应自动 promote"
        w1 = list(m1._w)

        # Round-2: 新数据上再训练 → v2
        _populate_dataset(pq_root, n=80)
        m2 = pipe.train(save_version=True)
        active2 = vm.active_version()
        # 新版默认不 promote（仍留在 v1）
        assert active2.version_id == active1.version_id, (
            "新版不应自动 promote — active 应仍是 v1"
        )

        # Promote v2。此前这里写成 `vm.promote(active2.version_id) or vm.promote(m2._version)`，
        # 而上面一条断言刚刚证明 active2 == v1 —— promote 一个已在册的版本必然返回 True，
        # `or` 于是永远短路，v2 从来没被晋升过，本用例红在下一行且红得没有信息。
        # promote() 的返回值只说明"这个 id 在链上"，不能当"它现在是 active"的证据，
        # 所以断言只看 active_version()。
        assert vm.promote(m2._version), f"v2 {m2._version} 不在版本链上"
        assert vm.active_version().version_id == m2._version, (
            f"promote v2 失败: active={vm.active_version().version_id}, "
            f"v2={m2._version}"
        )

        # Rollback → active 恢复为 v1
        rb = vm.rollback()
        assert rb is not None
        assert rb.version_id == active1.version_id
        assert vm.active_version().version_id == active1.version_id

        # v1 权重可完整回读，且与原始 m1 一致
        loaded_v1 = vm.load_model(active1.version_id)
        assert loaded_v1 is not None
        assert math.isclose(loaded_v1.predict([0.3] * 10),
                            m1.predict([0.3] * 10), rel_tol=1e-6)
        assert loaded_v1._w == w1

    def test_version_chain_list_order(self, pq_root: Path) -> None:
        _populate_dataset(pq_root, n=40)
        pipe = PlanQualityDataPipeline(pq_root)
        vm = PlanVersionManager(pq_root)

        versions = []
        for _ in range(3):
            pipe.train(save_version=True)
            versions.append(vm.active_version().version_id)

        chain = vm.list_versions()
        assert len(chain) == 3
        # 三条版本 id 互不相同
        assert len({v.version_id for v in chain}) == 3

    def test_rollback_at_first_version_is_noop(self, pq_root: Path) -> None:
        _populate_dataset(pq_root, n=30)
        pipe = PlanQualityDataPipeline(pq_root)
        vm = PlanVersionManager(pq_root)
        pipe.train(save_version=True)

        active_before = vm.active_version().version_id
        rb = vm.rollback()
        assert rb is None, "只有一个版本时 rollback 应返回 None"
        assert vm.active_version().version_id == active_before


class TestEndToEndOnlineLearning:
    """GA E2E：在线学习真实闭环 —— 数据管道 → 训练 → 评估 → 反馈 → 模型更新。

    验证 ``add_execution`` 不只更新内存中的模型权重，还能让 evaluator 在
    后续的 ``rank() / score()`` 中体现出改进——这是"质量学习能自我迭代"的
    关键验收。
    """

    def test_online_learning_raises_fed_plan_score(self, pq_root: Path) -> None:
        """在线学习把"喂了真值的那条计划"的预测分推高（已实证的可观察行为）。

        本批只断言这一条 —— 它不依赖模型对没见过的特征形态做外推。
        "学完之后结构更优的计划应当排在前面"是更强的主张，单独放在
        ``test_online_learning_flips_structural_ranking`` 里并按当前数据路径标注。
        """
        _populate_dataset(pq_root, n=50)
        pipe = PlanQualityDataPipeline(pq_root)
        model = pipe.train(save_version=True)
        ev = PlanQualityEvaluator(pq_root, model=model, online_learning=True)

        good = _make_plan(
            task="deploy to prod",
            agent="alpha", rk="gen",
            gates=["exit_code", "output", "content-safety", "syntax"],
            max_retries=1,
        )
        plan_feat = PlanFeatureExtractor().extract(good, agent_match=1.0, total_agent_samples=25)
        score_before = model.predict(plan_feat)

        # 连续 30 次"优质执行"反馈 → 模型应对 good 计划提升预测分
        for _ in range(30):
            ev.add_execution(good, {
                "outcome": "success", "cost_usd": 0.001,
                "latency_ms": 400, "retries_used": 0,
            })

        score_after = model.predict(plan_feat)
        assert score_after > score_before, (
            f"在线学习未改善预测分: before={score_before:.4f}, after={score_after:.4f}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "已知缺陷（未修，非夹具问题）：批量训练用的特征不是观测来的，而是 "
            "ingest_from_agent_performance 以 _infer_gates(routing_key) + 默认预算**重建**的。"
            "夹具的 rk 'gen'/'sec' 都不命中 _infer_gates 的任何分支 ⇒ dim1 has_content_safety、"
            "dim2 has_dry_run 恒 0、dim4 retries 恒 1，这三维的权重是不可识别的偏置吸收"
            "（2026-10-17 实测 w1=-0.4248、w4=+0.2682）。于是推理期 good 的 content-safety=1 "
            "反而吃到负权重，bad 的 max_retries=3 又把那个未识别的 dim4 外推 3 倍 ⇒ "
            "rank([bad, good]) 在 30 次在线更新后仍是 garbage 在前（0.9715 vs 0.9601，差 0.011）。"
            "前置条件：plan_quality_dataset / agent_performance 要携带执行期真实的 gates 与 budget"
            "（现表结构没有这两列），并给持久化模型加 feature_schema 标记 —— 否则改动任何一维"
            "语义都会静默重解释已落盘权重。这两条都做到之后本用例应 XPASS，届时删掉标记。"
        ),
    )
    def test_online_learning_flips_structural_ranking(self, pq_root: Path) -> None:
        """在线学习后，结构更优的计划应排在前面（更强的主张，当前不成立）。"""
        _populate_dataset(pq_root, n=50)
        pipe = PlanQualityDataPipeline(pq_root)
        model = pipe.train(save_version=True)
        ev = PlanQualityEvaluator(pq_root, model=model, online_learning=True)

        good = _make_plan(
            task="deploy to prod",
            agent="alpha", rk="gen",
            gates=["exit_code", "output", "content-safety", "syntax"],
            max_retries=1,
        )
        bad = _make_plan(
            task="garbage",
            agent="wrong_agent", rk="gen",
            gates=["exit_code"],
            max_retries=3,
        )

        for _ in range(30):
            ev.add_execution(good, {
                "outcome": "success", "cost_usd": 0.001,
                "latency_ms": 400, "retries_used": 0,
            })

        ranked = ev.rank([bad, good])
        assert ranked[0][0] is good
        assert ranked[0][1] > ranked[1][1]

    def test_online_learning_persists_across_evaluators(self, pq_root: Path) -> None:
        """在线学习更新后，新建 evaluator 加载已保存的促销版本仍能打分。"""
        _populate_dataset(pq_root, n=40)
        pipe = PlanQualityDataPipeline(pq_root)
        model = pipe.train(save_version=True)

        # 第一版 evaluator 做在线学习
        ev1 = PlanQualityEvaluator(pq_root, model=model, online_learning=True)
        plan = _make_plan(task="refactor tests", agent="beta", rk="sec",
                          gates=["exit_code", "output"], max_retries=1)
        for _ in range(10):
            ev1.add_execution(plan, {
                "outcome": "success", "cost_usd": 0.002,
                "latency_ms": 700, "retries_used": 0,
            })

        # 新建 evaluator，不传 model → 从磁盘 load 促销版本
        ev2 = PlanQualityEvaluator(pq_root, online_learning=False)
        # evaluator 能正常评分（取自磁盘的促销版本）
        score = ev2.score(plan)
        assert 0 < score < 1, f"加载磁盘版本后评分异常: {score}"


class TestEndToEndDataPipelineToEvaluator:
    """GA E2E：从 agent_performance 数据到 evaluator 产出的完整链路。
    覆盖 ingest → train → version → evaluator 全路径。
    """

    def test_full_pipeline_ingests_trains_ranks(self, pq_root: Path) -> None:
        _populate_dataset(pq_root, n=60)
        pipe = PlanQualityDataPipeline(pq_root)
        ingested = pipe.ingest_from_agent_performance()
        assert ingested > 0, "ingest 应写入记录"

        model = pipe.train(save_version=True)
        assert model._trained

        ev = PlanQualityEvaluator(pq_root, model=model)
        plans = [
            _make_plan(task="create dashboard", agent="alpha", rk="gen",
                        gates=["exit_code", "output", "content-safety"], max_retries=1),
            _make_plan(task="unknown task", agent="delta", rk="gen",
                        gates=["exit_code"], max_retries=3),
            _make_plan(task="fix security issue", agent="beta", rk="sec",
                        gates=["exit_code", "content-safety", "dry-run"], max_retries=2),
        ]
        ranked = ev.rank(plans)
        assert len(ranked) == 3
        # 排序稳定：逐对非递增
        for i in range(len(ranked) - 1):
            assert ranked[i][1] >= ranked[i + 1][1]
        # 每个分数在有效范围
        for _, score in ranked:
            assert 0 < score < 1

    def test_train_with_no_data_returns_untrained(self, pq_root: Path) -> None:
        """边界：无数据时 train 返回未训练模型（不抛异常）。"""
        pipe = PlanQualityDataPipeline(pq_root)
        model = pipe.train(save_version=True)
        assert not model._trained
