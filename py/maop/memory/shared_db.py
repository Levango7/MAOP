"""Memory 共享数据库访问层。

统一 ``MemoryManager`` 和 ``ThreeLayerMemory`` 的 DB 文件路径与术语命名，
消除双 DB 不通信问题。

背景
----
两套三层记忆实现原本各自维护独立的 DB 文件：

- ``MemoryManager`` (maop.memory.manager): 通过 ``MemoryStore`` 写入
  ``maop.db`` 的 ``memory_entries`` 表；自身另维护 ``consolidation_log`` 表。
- ``ThreeLayerMemory`` (maop.core.three_layer_memory): 写入独立的
  ``<root>/data/episodic.db`` 文件的 ``episodic_memory`` 表。

结果：用户在 chat 中存入的记忆（memory_entries），evolution_loop 看不到；
agent_performance 反馈（episodic_memory）无法回写到 chat 上下文。

解决方案
--------
1. **统一 DB 文件**：``ThreeLayerMemory`` 改用 ``get_memory_db_path()``
   返回的路径（unified 模式下为 ``maop.db``），与 ``MemoryStore`` 共享同一
   个 SQLite 文件。两套表（``memory_entries`` / ``episodic_memory``）schema
   不同但表名不冲突，可安全共存于同一 DB。
2. **术语映射**：``LAYER_ALIASES`` 将 ``episodic`` ↔ ``short_term``、
   ``semantic`` ↔ ``long_term`` 统一映射到 ``MemoryManager`` 的标准命名
   (working/short_term/long_term)。
3. **旧数据迁移**：``migrate_legacy_episodic_db()`` 在 ``ThreeLayerMemory``
   初始化时检查是否存在旧的 ``<root>/data/episodic.db``，若有则将其中的
   ``episodic_memory`` 表数据导入到新 DB，避免数据丢失。

注意
----
本模块只负责 DB 路径与术语映射，不修改两套实现的 schema 或外部 API。
两套类的外部接口（方法签名、行为）保持不变。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)

# 统一 DB 模块名。
# - unified 模式（默认）：映射到 maop.db
# - per-module 模式 (MAOP_DB_PER_MODULE=1)：映射到 memory.db
MEMORY_DB_NAME = "memory"


def get_memory_db_path() -> Path:
    """返回统一的 memory DB 路径。

    在 unified 模式下与 ``MemoryStore`` / ``MemoryManager`` 共享 ``maop.db``；
    在 per-module 模式下返回独立的 ``memory.db``。
    """
    return get_db_path(MEMORY_DB_NAME)


# 术语映射：ThreeLayerMemory 命名 ↔ MemoryManager 命名
# 统一映射到 MemoryManager 的标准命名（working/short_term/long_term）
LAYER_ALIASES: dict[str, str] = {
    "working": "working",
    "short_term": "short_term",
    "episodic": "short_term",   # episodic ↔ short_term
    "long_term": "long_term",
    "semantic": "long_term",    # semantic ↔ long_term
}


def normalize_layer_name(name: str) -> str:
    """将 layer 名称标准化为 working/short_term/long_term。

    接受两套实现的命名：
      - MemoryManager: working / short_term / long_term
      - ThreeLayerMemory: working / episodic / semantic

    Examples
    --------
    >>> normalize_layer_name("episodic")
    'short_term'
    >>> normalize_layer_name("semantic")
    'long_term'
    >>> normalize_layer_name("working")
    'working'
    >>> normalize_layer_name("Short_Term")
    'short_term'
    """
    return LAYER_ALIASES.get(name.lower(), name.lower())


def denormalize_layer_name(name: str) -> str:
    """将标准 layer 名称反向映射为 ThreeLayerMemory 命名。

    用于 ThreeLayerMemory 内部把 MemoryManager 的命名转回 episodic/semantic。
    working 保持不变；short_term → episodic；long_term → semantic。
    """
    mapping = {
        "working": "working",
        "short_term": "episodic",
        "long_term": "semantic",
    }
    return mapping.get(name.lower(), name.lower())


def migrate_legacy_episodic_db(root_dir: str | Path) -> int:
    """迁移旧的独立 ``episodic.db`` 数据到统一 DB。

    在 ``ThreeLayerMemory`` 改用统一 DB 路径后，旧的
    ``<root>/data/episodic.db`` 文件中的 ``episodic_memory`` 表数据需要导入
    到新位置（``maop.db``）。本函数幂等：

    1. 旧文件不存在 → 直接返回 0。
    2. 旧文件存在但无 ``episodic_memory`` 表 → 返回 0。
    3. 旧文件存在且有数据 → 按行 INSERT OR IGNORE 到新 DB，返回迁移行数。
    4. 迁移成功后**不**删除旧文件，由调用方决定是否清理。

    Parameters
    ----------
    root_dir : str | Path
        MAOP 项目根目录，用于定位 ``<root>/data/episodic.db``。

    Returns
    -------
    int
        实际迁移的行数。
    """
    legacy_path = Path(root_dir) / "data" / "episodic.db"
    if not legacy_path.exists():
        return 0

    new_path = get_memory_db_path()
    if new_path.resolve() == legacy_path.resolve():
        # 已经是同一个文件，无需迁移
        return 0

    try:
        with sqlite_connect(legacy_path, foreign_keys=False, wal=False) as src:
            # 检查旧 DB 是否有 episodic_memory 表
            cur = src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='episodic_memory'"
            )
            if cur.fetchone() is None:
                return 0

            rows = src.execute("SELECT * FROM episodic_memory").fetchall()
            if not rows:
                return 0

            cols = [d[0] for d in src.execute("SELECT * FROM episodic_memory LIMIT 1").description]
            placeholders = ",".join("?" * len(cols))
            cols_csv = ",".join(cols)

        # 导入到新 DB（INSERT OR IGNORE 避免主键冲突）
        migrated = 0
        with sqlite_connect(new_path, foreign_keys=False) as dst:
            # 确保目标表存在（schema 由 ThreeLayerMemory._init_episodic_db 创建）
            # 这里只做数据导入，schema 创建由调用方负责
            for row in rows:
                try:
                    dst.execute(
                        f"INSERT OR IGNORE INTO episodic_memory ({cols_csv}) VALUES ({placeholders})",
                        tuple(row),
                    )
                    if dst.total_changes > 0:
                        migrated += 1
                except sqlite3.Error as exc:
                    logger.warning("[shared_db] 迁移行失败: %s", exc)

        logger.info(
            "[shared_db] 从旧 %s 迁移了 %d 行 episodic_memory 数据到 %s",
            legacy_path, migrated, new_path,
        )
        return migrated
    except Exception as exc:
        logger.warning("[shared_db] 迁移旧 episodic.db 失败: %s", exc)
        return 0
