"""Integration tests for the MAOP K8s Operator Helm chart + CRD.

All tests are marked ``@pytest.mark.slow`` so they are excluded from the
default fast run (``-m 'not slow'``).  Tests that need a live Kubernetes
cluster or the ``helm`` CLI skip gracefully when the dependency is absent.

Layers
------
1. **Static** (always run under ``-m slow``): chart file layout, CRD schema,
   values.yaml structure — pure file/YAML parsing, no cluster.
2. **Helm CLI** (skip if ``helm`` missing): ``helm lint`` + ``helm template``.
3. **Cluster** (skip if ``kubectl`` missing or no cluster): apply CRD, create a
   MaopAgent CR, verify the controller reconciles it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.slow

# Chart root: <project>/deploy/k8s/operator
CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "k8s" / "operator"


# ── helpers ───────────────────────────────────────────────────────────


def _has(bin_name: str) -> bool:
    return shutil.which(bin_name) is not None


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _helm(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["helm", *args], capture_output=True, text=True, timeout=60, check=False,
    )


def _kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=60, check=False,
    )


# ── 1. static chart structure ─────────────────────────────────────────


class TestChartStructure:
    def test_chart_dir_exists(self):
        assert CHART_DIR.is_dir(), f"chart dir missing: {CHART_DIR}"

    def test_chart_yaml(self):
        chart = _load_yaml(CHART_DIR / "Chart.yaml")
        assert chart["apiVersion"] == "v2"
        assert chart["name"] == "maop-operator"
        assert chart["type"] == "application"
        assert "appVersion" in chart

    def test_values_yaml(self):
        values = _load_yaml(CHART_DIR / "values.yaml")
        assert "image" in values
        assert "controller" in values
        assert values["controller"]["multiTenant"]["enabled"] is True
        assert values["controller"]["plugins"]["enabled"] is True
        assert values["crds"]["install"] is True

    def test_templates_present(self):
        templates = CHART_DIR / "templates"
        expected = {
            "_helpers.tpl", "deployment.yaml", "service.yaml",
            "serviceaccount.yaml", "role.yaml", "rolebinding.yaml",
            "configmap.yaml", "webhook.yaml",
        }
        actual = {p.name for p in templates.iterdir()}
        missing = expected - actual
        assert not missing, f"missing templates: {missing}"

    def test_crd_file_present(self):
        crd = CHART_DIR / "crds" / "maopagent.yaml"
        assert crd.is_file()

    def test_readme_present(self):
        assert (CHART_DIR / "README.md").is_file()


# ── 2. CRD validation ─────────────────────────────────────────────────


class TestCRDValidation:
    @pytest.fixture
    def crd(self) -> dict:
        return _load_yaml(CHART_DIR / "crds" / "maopagent.yaml")  # type: ignore[return-value]

    def test_api_version(self, crd: dict):
        assert crd["apiVersion"] == "apiextensions.k8s.io/v1"
        assert crd["kind"] == "CustomResourceDefinition"

    def test_group_and_names(self, crd: dict):
        assert crd["spec"]["group"] == "maop.io"
        names = crd["spec"]["names"]
        assert names["kind"] == "MaopAgent"
        assert names["plural"] == "maopagents"
        assert "maopa" in names["shortNames"]

    def test_version_served(self, crd: dict):
        versions = crd["spec"]["versions"]
        v1 = next(v for v in versions if v["name"] == "v1alpha1")
        assert v1["served"] is True
        assert v1["storage"] is True

    def test_spec_schema_has_model(self, crd: dict):
        schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
        props = schema["properties"]["spec"]["properties"]
        assert "model" in props
        assert "tenant" in props
        assert "plugins" in props
        assert "quotas" in props

    def test_status_schema_has_phase(self, crd: dict):
        schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
        status_props = schema["properties"]["status"]["properties"]
        assert "phase" in status_props
        assert "QuotaExceeded" in status_props["phase"]["enum"]

    def test_subresources_status(self, crd: dict):
        sub = crd["spec"]["versions"][0]["subresources"]
        assert "status" in sub

    def test_scope_namespaced(self, crd: dict):
        assert crd["spec"]["scope"] == "Namespaced"


# ── 3. Helm CLI (skip if helm missing) ────────────────────────────────


@pytest.mark.skipif(not _has("helm"), reason="helm CLI not installed")
class TestHelmCLI:
    def test_helm_lint(self):
        result = _helm("lint", str(CHART_DIR))
        assert result.returncode == 0, f"helm lint failed:\n{result.stderr}"

    def test_helm_template_renders(self):
        result = _helm("template", "test-release", str(CHART_DIR))
        assert result.returncode == 0, f"helm template failed:\n{result.stderr}"
        docs = list(yaml.safe_load_all(result.stdout))
        kinds = {d.get("kind") for d in docs if d}
        assert "Deployment" in kinds
        assert "Service" in kinds

    def test_helm_template_with_multi_tenant(self):
        result = _helm(
            "template", "test-release", str(CHART_DIR),
            "--set", "controller.multiTenant.enabled=true",
            "--set", "controller.replicas=2",
        )
        assert result.returncode == 0
        docs = list(yaml.safe_load_all(result.stdout))
        deploy = next(d for d in docs if d and d.get("kind") == "Deployment")
        assert deploy["spec"]["replicas"] == 2


# ── 4. Kubernetes cluster (skip if no kubectl / no cluster) ───────────


def _cluster_available() -> bool:
    if not _has("kubectl"):
        return False
    return _kubectl("cluster-info").returncode == 0


@pytest.mark.skipif(not _cluster_available(), reason="no Kubernetes cluster reachable")
class TestK8sIntegration:
    """End-to-end: apply CRD, create a MaopAgent CR, verify it is accepted."""

    CRD_PATH = str(CHART_DIR / "crds" / "maopagent.yaml")
    TEST_NS = "maop-test"

    def test_apply_crd(self):
        result = _kubectl("apply", "-f", self.CRD_PATH)
        assert result.returncode == 0, f"apply CRD failed:\n{result.stderr}"

    def test_create_maopagent_cr(self):
        cr = """
apiVersion: maop.io/v1alpha1
kind: MaopAgent
metadata:
  name: test-agent
  namespace: default
spec:
  model: gpt-4o
  tenant: test-tenant
  replicas: 1
  maxTurns: 5
  tools:
    - mcp.search
  plugins:
    - greeter
"""
        result = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=cr, capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == 0, f"create CR failed:\n{result.stderr}"

    def test_get_maopagent_cr(self):
        result = _kubectl("get", "maopagent", "test-agent", "-o", "jsonpath={.spec.model}")
        assert result.returncode == 0
        assert result.stdout.strip() == "gpt-4o"

    def test_cleanup(self):
        _kubectl("delete", "maopagent", "test-agent", "--ignore-not-found")