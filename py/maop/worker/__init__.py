"""MAOP Worker — Background service entry points for cloud-native deployment.

Submodules
----------
* ``queue_worker`` — background queue consumer (human approvals, maintenance).
* ``agent_executor`` — agent execution worker.
* ``distributed_worker`` — F1-01 distributed worker (Redis Streams consumer).
"""

from __future__ import annotations
