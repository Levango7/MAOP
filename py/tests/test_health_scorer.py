
"""HealthScorer 白盒测试.

覆盖：记录 / 评分计算 / 滑动窗口 / 排序 / 重置 / 持久化 / 线程安全 / 异常。
每个测试使用 tmp_path 隔离 SQLite，不触碰真实 DB。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from maop.core.agent.ops.health_scorer import HealthScore, HealthScorer  # noqa: F401
from tests.thread_join_guard import join_all

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def scorer(tmp_path: Path) -> HealthScorer:
    """使用隔离 tmp_path DB 的全新 HealthScorer。"""
    return HealthScorer(db_path=tmp_path / "health.db", window_size=100)


# ── 1. 基本记录与评分 ────────────────────────────────────────────


class TestRecordAndScore:
    """record_result 与 get_score 的基本行为。"""

    def test_no_records_returns_zero_score(self, scorer: HealthScorer) -> None:
        """无调用记录的 Agent 返回零分评分。"""
        score = scorer.get_score("agent-a")
        assert score.agent_name == "agent-a"
        assert score.score == 0
        assert score.success_rate == 0.0
        assert score.avg_response_time_s == 0.0
        assert score.avg_user_rating == 0.0
        assert score.total_calls == 0

    def test_single_success_record(self, scorer: HealthScorer) -> None:
        """单次成功调用应正确计算评分。"""
        scorer.record_result("agent-a", success=True, response_time_s=0.5)

        score = scorer.get_score("agent-a")
        assert score.agent_name == "agent-a"
        assert score.total_calls == 1
        assert score.success_rate == 1.0
        assert score.avg_response_time_s == 0.5
        # 成功率40 + 响应时间30(<=1s) + 用户评分0 + 调用量1/100*10=0.1
        # = 40 + 30 + 0 + 0.1 = 70.1 → round = 70
        assert score.score == 70

    def test_single_failure_record(self, scorer: HealthScorer) -> None:
        """单次失败调用的评分应低于成功调用。"""
        scorer.record_result("agent-a", success=False, response_time_s=0.5)

        score = scorer.get_score("agent-a")
        assert score.success_rate == 0.0
        # 成功率0 + 响应时间30 + 用户评分0 + 调用量0.1
        # = 0 + 30 + 0 + 0.1 = 30.1 → round = 30
        assert score.score == 30

    def test_mixed_success_failure(self, scorer: HealthScorer) -> None:
        """混合成功/失败调用应正确计算成功率。"""
        for _ in range(8):
            scorer.record_result("agent-a", success=True, response_time_s=0.5)
        for _ in range(2):
            scorer.record_result("agent-a", success=False, response_time_s=0.5)

        score = scorer.get_score("agent-a")
        assert score.total_calls == 10
        assert score.success_rate == pytest.approx(0.8)
        # 成功率0.8*40=32 + 响应时间30 + 调用量10/100*10=1
        # = 32 + 30 + 0 + 1 = 63
        assert score.score == 63


# ── 2. 评分算法各维度 ────────────────────────────────────────────


class TestScoreDimensions:
    """评分算法各维度（成功率/响应时间/用户评分/调用量）。"""

    def test_response_time_fast(self, scorer: HealthScorer) -> None:
        """快速响应(<=1s)得30分。"""
        scorer.record_result("agent-a", success=True, response_time_s=0.8)
        score = scorer.get_score("agent-a")
        # 成功率40 + 响应时间30 + 调用量0.1 = 70.1 → 70
        assert score.score == 70

    def test_response_time_medium(self, scorer: HealthScorer) -> None:
        """中速响应(<=5s)得20分。"""
        scorer.record_result("agent-a", success=True, response_time_s=3.0)
        score = scorer.get_score("agent-a")
        # 成功率40 + 响应时间20 + 调用量0.1 = 60.1 → 60
        assert score.score == 60

    def test_response_time_slow(self, scorer: HealthScorer) -> None:
        """慢速响应(<=10s)得10分。"""
        scorer.record_result("agent-a", success=True, response_time_s=8.0)
        score = scorer.get_score("agent-a")
        # 成功率40 + 响应时间10 + 调用量0.1 = 50.1 → 50
        assert score.score == 50

    def test_response_time_very_slow(self, scorer: HealthScorer) -> None:
        """超慢响应(>10s)得0分。"""
        scorer.record_result("agent-a", success=True, response_time_s=15.0)
        score = scorer.get_score("agent-a")
        # 成功率40 + 响应时间0 + 调用量0.1 = 40.1 → 40
        assert score.score == 40

    def test_user_rating_dimension(self, scorer: HealthScorer) -> None:
        """用户评分维度：avg_rating/5*20。"""
        scorer.record_result(
            "agent-a", success=True, response_time_s=0.5, user_rating=4,
        )
        score = scorer.get_score("agent-a")
        assert score.avg_user_rating == 4.0
        # 成功率40 + 响应时间30 + 用户评分4/5*20=16 + 调用量0.1
        # = 40 + 30 + 16 + 0.1 = 86.1 → 86
        assert score.score == 86

    def test_call_volume_dimension(self, scorer: HealthScorer) -> None:
        """调用量维度：min(total/100, 1)*10，调用越多越可信。"""
        # 记录 100 次成功调用，调用量满分
        for _ in range(100):
            scorer.record_result("agent-a", success=True, response_time_s=0.5)

        score = scorer.get_score("agent-a")
        # 成功率40 + 响应时间30 + 用户评分0 + 调用量min(100/100,1)*10=10
        # = 40 + 30 + 0 + 10 = 80
        assert score.score == 80
        assert score.total_calls == 100


# ── 3. 滑动窗口 ──────────────────────────────────────────────────


class TestSlidingWindow:
    """滑动窗口行为。"""

    def test_window_evicts_old_records(self, tmp_path: Path) -> None:
        """超过 window_size 的旧记录被驱逐。"""
        scorer = HealthScorer(db_path=tmp_path / "h.db", window_size=5)
        # 记录 5 次成功 + 3 次失败 = 8 次，窗口只保留最后 5 次（全失败）
        for _ in range(5):
            scorer.record_result("agent-a", success=True, response_time_s=0.5)
        for _ in range(3):
            scorer.record_result("agent-a", success=False, response_time_s=0.5)

        score = scorer.get_score("agent-a")
        # 窗口内只有最后 5 条（3 失败 + 2 成功被驱逐）
        # 实际窗口：5 成功 + 3 失败，但 maxlen=5，所以保留最后 5 条 = 3 失败 + 2 成功
        # 等等，deque maxlen=5，append 8 次，保留最后 5 次 = 3 失败 + 2 成功
        assert score.total_calls == 5
        # 2 成功 / 5 = 0.4
        assert score.success_rate == pytest.approx(0.4)

    def test_window_size_validation(self, tmp_path: Path) -> None:
        """window_size 必须为正整数。"""
        with pytest.raises(ValueError, match="window_size"):
            HealthScorer(db_path=tmp_path / "h.db", window_size=0)
        with pytest.raises(ValueError, match="window_size"):
            HealthScorer(db_path=tmp_path / "h.db", window_size=-1)


# ── 4. 多 Agent 与排序 ───────────────────────────────────────────


class TestMultipleAgents:
    """多 Agent 评分与排序。"""

    def test_get_all_scores(self, scorer: HealthScorer) -> None:
        """get_all_scores 返回所有有记录的 Agent。"""
        scorer.record_result("agent-a", success=True, response_time_s=0.5)
        scorer.record_result("agent-b", success=False, response_time_s=2.0)

        scores = scorer.get_all_scores()
        assert len(scores) == 2
        names = {s.agent_name for s in scores}
        assert names == {"agent-a", "agent-b"}

    def test_get_ranking_sorted(self, scorer: HealthScorer) -> None:
        """get_ranking 按评分降序排列。"""
        # agent-a: 高分（成功+快速）
        scorer.record_result("agent-a", success=True, response_time_s=0.5)
        # agent-b: 低分（失败+慢速）
        scorer.record_result("agent-b", success=False, response_time_s=15.0)

        ranking = scorer.get_ranking()
        assert len(ranking) == 2
        assert ranking[0].score >= ranking[1].score
        assert ranking[0].agent_name == "agent-a"
        assert ranking[1].agent_name == "agent-b"


# ── 5. 重置 ──────────────────────────────────────────────────────


class TestReset:
    """reset_score 行为。"""

    def test_reset_score(self, scorer: HealthScorer) -> None:
        """重置后 Agent 评分归零。"""
        scorer.record_result("agent-a", success=True, response_time_s=0.5)
        assert scorer.get_score("agent-a").total_calls == 1

        scorer.reset_score("agent-a")
        score = scorer.get_score("agent-a")
        assert score.total_calls == 0
        assert score.score == 0

    def test_reset_nonexistent_silent(self, scorer: HealthScorer) -> None:
        """重置不存在的 Agent 应静默忽略（不抛异常）。"""
        scorer.reset_score("nonexistent")  # 不应抛异常


# ── 6. 持久化 ────────────────────────────────────────────────────


class TestPersistence:
    """评分记录持久化。"""

    def test_persistence_reload(self, tmp_path: Path) -> None:
        """记录持久化到 SQLite，重新加载后保留。"""
        db_path = tmp_path / "health.db"
        scorer1 = HealthScorer(db_path=db_path, window_size=100)
        scorer1.record_result("agent-a", success=True, response_time_s=0.5)
        scorer1.record_result("agent-a", success=False, response_time_s=1.0)

        # 用同一 DB 路径创建新实例，验证记录已持久化
        scorer2 = HealthScorer(db_path=db_path, window_size=100)
        score = scorer2.get_score("agent-a")
        assert score.total_calls == 2
        assert score.success_rate == pytest.approx(0.5)

    def test_persistence_reset(self, tmp_path: Path) -> None:
        """重置后重新加载，记录确实被删除。"""
        db_path = tmp_path / "health.db"
        scorer1 = HealthScorer(db_path=db_path, window_size=100)
        scorer1.record_result("agent-a", success=True, response_time_s=0.5)
        scorer1.reset_score("agent-a")

        scorer2 = HealthScorer(db_path=db_path, window_size=100)
        score = scorer2.get_score("agent-a")
        assert score.total_calls == 0


# ── 7. 异常 ──────────────────────────────────────────────────────


class TestValidation:
    """输入参数校验。"""

    def test_negative_response_time_raises(self, scorer: HealthScorer) -> None:
        """负响应时间应抛 ValueError。"""
        with pytest.raises(ValueError, match="response_time_s"):
            scorer.record_result("agent-a", success=True, response_time_s=-1.0)

    def test_invalid_user_rating_raises(self, scorer: HealthScorer) -> None:
        """超出 0-5 的用户评分应抛 ValueError。"""
        with pytest.raises(ValueError, match="user_rating"):
            scorer.record_result("agent-a", success=True, response_time_s=0.5, user_rating=6)
        with pytest.raises(ValueError, match="user_rating"):
            scorer.record_result("agent-a", success=True, response_time_s=0.5, user_rating=-1)


# ── 8. 线程安全 ──────────────────────────────────────────────────


class TestThreadSafety:
    """多线程并发记录。"""

    @pytest.mark.timeout(240)
    def test_concurrent_record(self, scorer: HealthScorer) -> None:
        """多线程并发记录不应丢失数据或抛异常。"""
        num_threads = 10
        records_per_thread = 50

        def worker() -> None:
            for _ in range(records_per_thread):
                scorer.record_result(
                    "agent-a", success=True, response_time_s=0.5,
                )

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        join_all(threads, 120.0)

        score = scorer.get_score("agent-a")
        # window_size=100，所以最多保留 100 条
        assert score.total_calls == 100
        assert score.success_rate == 1.0