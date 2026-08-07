"""Dashboard service layer.

Business logic extracted from router handlers to keep routers thin
(parameter parsing → service call → response). Services own the
orchestration of core modules and are importable without FastAPI.
"""