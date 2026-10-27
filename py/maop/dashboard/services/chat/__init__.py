"""Chat & Communication sub-packages.

Split out of ``chat_service`` (file-size ratchet):

- ``streaming.py`` — SSE streaming generators + event classification
- ``feedback.py`` — user feedback CRUD (SQLite, IDOR-protected)

Both are re-exported by the parent ``chat_service`` module; this
package's ``__init__`` only aggregates the forwarded names for direct
sub-package imports.
"""

from __future__ import annotations

from maop.dashboard.services.chat.feedback import (
    FEEDBACK_COLUMNS,
    VALID_TARGET_TYPES,
    check_feedback_ownership,
    create_feedback,
    delete_feedback,
    ensure_schema,
    export_feedback_rows,
    format_feedback_csv,
    get_feedback_summary,
    list_feedback,
    new_feedback_id,
    reset_schema_for_tests,
    row_to_dict,
    update_feedback,
)
from maop.dashboard.services.chat.streaming import (
    classify_agent_event,
    get_streamer,
    list_active_streams,
    stream_dag_progress_chunks,
    stream_from_event_bus,
    stream_from_streamer,
    stream_global_state_chunks,
)

__all__ = [
    "FEEDBACK_COLUMNS",
    "VALID_TARGET_TYPES",
    "check_feedback_ownership",
    "classify_agent_event",
    "create_feedback",
    "delete_feedback",
    "ensure_schema",
    "export_feedback_rows",
    "format_feedback_csv",
    "get_feedback_summary",
    "get_streamer",
    "list_active_streams",
    "list_feedback",
    "new_feedback_id",
    "reset_schema_for_tests",
    "row_to_dict",
    "stream_dag_progress_chunks",
    "stream_from_event_bus",
    "stream_from_streamer",
    "stream_global_state_chunks",
    "update_feedback",
]
