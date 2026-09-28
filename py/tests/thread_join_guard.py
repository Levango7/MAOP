"""并发用例的统一收口：有界等待线程，超时就报可读失败。

CI 的 unit job 用 ``--timeout=60 --reruns=3``（pytest-timeout）。裸 ``t.join()``
在真死锁或调度退化时表现为"永久挂起 + 一段线程栈 dump"，看不出是谁卡住、
也看不出是慢还是锁死；windows-3.13 就因此在 ``test_result_cache`` 上白烧了
4 次 rerun。这里把等待有界化，并把仍未结束的线程名报出来。

配合用法：调用方所在用例需带 ``@pytest.mark.timeout(240)``，让本断言（默认
120s）先于 pytest-timeout 触发 —— 否则超时杀进程只会又留下一段栈。
"""

from __future__ import annotations

import threading

DEFAULT_JOIN_TIMEOUT_S = 120.0


def join_all(
    threads: list[threading.Thread],
    timeout_s: float = DEFAULT_JOIN_TIMEOUT_S,
) -> None:
    """有界等待所有线程；未在 ``timeout_s`` 内结束就直接失败。"""
    for thread in threads:
        thread.join(timeout=timeout_s)
    alive = [t.name for t in threads if t.is_alive()]
    if alive:
        raise AssertionError(
            f"{len(alive)}/{len(threads)} 个工作线程未在 {timeout_s:.0f}s 内结束：{alive}"
        )
