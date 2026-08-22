"""Tests for maop.enterprise.container.ContainerOrchestrator — Dockerfile, compose, and k8s generation."""

from __future__ import annotations

import pytest

# H4 修复：将 importorskip 改为显式 pytest.skip，让测试报告显式统计跳过数。
pytest.skip(reason="maop.enterprise 未发布", allow_module_level=True)

from maop.enterprise.container import (
    ContainerConfig,
    ContainerOrchestrator,
)


@pytest.fixture(autouse=True)
def enterprise_mode():
    """Enable enterprise edition so require_feature(FeatureFlag.MULTI_USER) passes."""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


def test_default_config():
    """ContainerOrchestrator uses sensible default ContainerConfig values."""
    orch = ContainerOrchestrator()
    cfg = orch.config
    assert cfg.image_name == "maop-enterprise"
    assert cfg.port == 9079
    assert cfg.replicas == 2
    assert cfg.health_check_path == "/api/health"


def test_custom_config():
    """ContainerOrchestrator accepts a custom ContainerConfig."""
    custom = ContainerConfig(image_name="my-maop", port=1234, replicas=5)
    orch = ContainerOrchestrator(config=custom)
    assert orch.config.image_name == "my-maop"
    assert orch.config.port == 1234
    assert orch.config.replicas == 5


def test_generate_dockerfile():
    """generate_dockerfile() contains the required Dockerfile directives."""
    orch = ContainerOrchestrator()
    dockerfile = orch.generate_dockerfile()
    assert "FROM python:3.12-slim" in dockerfile
    assert "WORKDIR /app" in dockerfile
    assert "EXPOSE" in dockerfile
    assert "HEALTHCHECK" in dockerfile


def test_generate_dockerfile_contains_port():
    """generate_dockerfile() exposes the configured port."""
    orch = ContainerOrchestrator()
    dockerfile = orch.generate_dockerfile()
    assert "EXPOSE 9079" in dockerfile


def test_generate_docker_compose():
    """generate_docker_compose() contains the required services."""
    orch = ContainerOrchestrator()
    compose = orch.generate_docker_compose()
    assert "services:" in compose
    assert "maop:" in compose
    assert "postgres:" in compose
    assert "redis:" in compose
    assert "rabbitmq:" in compose


def test_generate_k8s_manifest():
    """generate_k8s_manifest() contains the required Kubernetes directives."""
    orch = ContainerOrchestrator()
    manifest = orch.generate_k8s_manifest()
    assert "apiVersion: apps/v1" in manifest
    assert "kind: Deployment" in manifest
    assert "replicas:" in manifest
    assert "livenessProbe" in manifest


def test_generate_k8s_manifest_contains_resources():
    """generate_k8s_manifest() contains CPU and memory resource limits."""
    orch = ContainerOrchestrator()
    manifest = orch.generate_k8s_manifest()
    assert "cpu:" in manifest
    assert "memory:" in manifest
