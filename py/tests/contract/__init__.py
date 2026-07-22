"""MAOP Contract Tests — verify API contracts between frontend and backend.

These tests validate that:
1. All server.py endpoints return the expected JSON schema
2. Frontend app.js API calls match backend response shapes
3. Model management APIs return correct Pydantic-serialized dicts
4. Control plane actions produce audit events
"""
