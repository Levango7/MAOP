"""MAOP core modules.

Infrastructure layer for the MAOP agent orchestration platform.

Key modules:
  - cache: LRU+TTL in-memory cache with pin/unpin and three-protection
  - db_utils: SQLite connection management (sqlite_connect)
  - three_layer_memory: Working/Episodic/Semantic memory with FTS5 and access-count consolidation
  - llm_provider: LLM provider factory with fallback chain
  - budget_guard: Daily token/cost budget enforcement
  - tool_audit: Tool invocation audit logging
  - agent_proxy: Adapter pattern for external agent integration
  - worktree: Branch management for parallel task execution
  - subagent_manager: Async SubAgent spawn/wait/cancel
  - mcp_hub: MCP Hub with stdio/SSE/WebSocket transport
  - skill_version: Skill versioning with hot-reload and intent matching
  - hook_manager: Event hook system
  - evolution_loop: Self-evolution reflection cycle
  - error_ledger: Error pattern tracking
  - self_heal: Self-healing engine
  - vector: Vector store for semantic search
  - cost_tracker: LLM cost tracking
"""
