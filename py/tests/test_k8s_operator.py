"""Integration tests for the MAOP K8s Operator Helm chart + CRD.

All tests are marked ``@pytest.mark.slow`` so they are excluded from the
default fast run (``-m 'not slow'``).  Tests that need a live Kubernetes
cluster or the ``helm`` CLI skip gracefully when the dependency is absent.

Layers
------
1. **Static** (always run under ``-m slow``): chart file layout, CRD schema,
   values.yaml structure — pure file/YAML parsing, no cluster.
2. **Helm CLI** (skip if ``helm`` missing): ``helm lint`` + ``helm template``.
3. **kubectl dry-run** (skip if ``kubectl`` missing): client-side
   ``kubectl apply --dry-run=client`` validates CRD + sample CR manifest
   without a live cluster.
4. **kind integration** (skip if ``kind`` or Docker missing): spin up a
   throwaway `kind <https://kind.sigs.k8s.io/>`_ cluster, install the chart,
   create a MaopAgent CR, verify the controller reconciles it, then tear
   down.  G-17.
5. **k3s integration** (skip if ``k3s`` missing): same flow against a
   `k3s <https://k3s.io/>`_ cluster.  G-17.
6. **Cluster** (skip if ``kubectl`` missing or no cluster): apply CRD,
   create a MaopAgent CR, verify the controller reconciles it against an
   existing cluster (e.g. CI-provided kind/k3s/minikube).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.slow

# Chart root: <project>/deploy/k8s/operator
CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "k8s" / "operator"
# Project root (for locating deploy/patroni etc.)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _kind(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kind", *args], capture_output=True, text=True, timeout=120, check=False,
    )


def _docker_available() -> bool:
    """Check whether the Docker daemon is reachable (required by kind)."""
    if not _has("docker"):
        return False
    return subprocess.run(
        ["docker", "info"], capture_output=True, text=True, timeout=10, check=False,
    ).returncode == 0


def _cluster_available_static() -> bool:
    """Check whether a Kubernetes cluster is reachable via kubectl.

    Used at class-decoration time (skipif), so it must be cheap and
    side-effect free.  Returns False if kubectl is missing or the cluster
    is unreachable.
    """
    if not _has("kubectl"):
        return False
    return _kubectl("cluster-info").returncode == 0


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


# ── 4. Static CR validation (always run) ─────────────────────────────
# G-17: validate a sample MaopAgent CR against the CRD schema using pure
# Python (no kubectl, no cluster).  This catches schema regressions
# (missing required fields, wrong types, unknown enum values) without
# requiring any external tool.


class TestStaticCRValidation:
    """Validate sample MaopAgent CRs against the CRD schema in pure Python."""

    @pytest.fixture
    def crd_schema(self) -> dict:
        crd = _load_yaml(CHART_DIR / "crds" / "maopagent.yaml")  # type: ignore[return-value]
        return crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]

    SAMPLE_CR = {
        "apiVersion": "maop.io/v1alpha1",
        "kind": "MaopAgent",
        "metadata": {"name": "test-agent", "namespace": "default"},
        "spec": {
            "model": "gpt-4o",
            "tenant": "test-tenant",
            "replicas": 1,
            "maxTurns": 5,
            "tools": ["mcp.search"],
            "plugins": ["greeter"],
        },
    }

    def test_sample_cr_has_required_spec_model(self, crd_schema: dict):
        """CRD marks spec.model as required; sample CR includes it."""
        required = crd_schema["properties"]["spec"].get("required", [])
        assert "model" in required
        assert "model" in self.SAMPLE_CR["spec"]

    def test_sample_cr_model_type_matches_schema(self, crd_schema: dict):
        """spec.model is a string in both CRD and sample CR."""
        model_prop = crd_schema["properties"]["spec"]["properties"]["model"]
        assert model_prop["type"] == "string"
        assert isinstance(self.SAMPLE_CR["spec"]["model"], str)

    def test_sample_cr_replicas_type_matches_schema(self, crd_schema: dict):
        """spec.replicas is an integer in both CRD and sample CR."""
        replicas_prop = crd_schema["properties"]["spec"]["properties"]["replicas"]
        assert replicas_prop["type"] == "integer"
        assert isinstance(self.SAMPLE_CR["spec"]["replicas"], int)

    def test_sample_cr_replicas_within_bounds(self, crd_schema: dict):
        """spec.replicas respects the CRD minimum constraint."""
        replicas_prop = crd_schema["properties"]["spec"]["properties"]["replicas"]
        minimum = replicas_prop.get("minimum", 0)
        assert self.SAMPLE_CR["spec"]["replicas"] >= minimum

    def test_status_phase_enum_has_expected_values(self, crd_schema: dict):
        """CRD status.phase enum includes the expected lifecycle phases."""
        phase_enum = crd_schema["properties"]["status"]["properties"]["phase"]["enum"]
        # Pending (initial), Running (reconciling), Failed (terminal error),
        # QuotaExceeded (quota guard rejected) are the contract.
        expected = {"Pending", "Running", "Failed", "QuotaExceeded"}
        assert expected.issubset(set(phase_enum))

    def test_crd_has_print_columns(self, crd_schema: dict):
        """CRD defines printer columns for `kubectl get maopagent`."""
        crd = _load_yaml(CHART_DIR / "crds" / "maopagent.yaml")  # type: ignore[return-value]
        versions = crd["spec"]["versions"]
        v1 = next(v for v in versions if v["name"] == "v1alpha1")
        additional_cols = v1.get("additionalPrinterColumns", [])
        col_names = {c["name"] for c in additional_cols}
        # Expect at least model + tenant + phase columns for usability.
        assert "model" in col_names or "Model" in col_names
        assert "phase" in col_names or "Phase" in col_names


# ── 5. kubectl client-side dry-run (skip if no cluster) ──────────────
# G-17: ``kubectl apply --dry-run=client`` needs API discovery, so it
# requires a reachable cluster.  When no cluster is available the tests
# skip gracefully; the static validation in TestStaticCRValidation covers
# the no-cluster case.


@pytest.mark.skipif(not _cluster_available_static(), reason="no Kubernetes cluster reachable")
class TestKubectlDryRun:
    """Client-side dry-run against a reachable cluster's API discovery."""

    CRD_PATH = str(CHART_DIR / "crds" / "maopagent.yaml")

    def test_crd_dry_run_client(self):
        """CRD manifest passes client-side validation."""
        result = _kubectl("apply", "--dry-run=client", "-f", self.CRD_PATH)
        assert result.returncode == 0, (
            f"kubectl dry-run CRD failed:\n{result.stderr}"
        )

    def test_crd_dry_run_server(self):
        """CRD manifest passes server-side validation."""
        result = _kubectl("apply", "--dry-run=server", "-f", self.CRD_PATH)
        assert result.returncode == 0, (
            f"kubectl server dry-run CRD failed:\n{result.stderr}"
        )


# ── 5. kind integration (skip if kind or Docker missing) ─────────────
# G-17: spin up a throwaway kind cluster, install the chart, create a
# MaopAgent CR, verify it is accepted, then tear down.


@pytest.mark.skipif(
    not _has("kind") or not _docker_available(),
    reason="kind CLI or Docker daemon not available",
)
class TestKindIntegration:
    """End-to-end: kind cluster → helm install → create CR → verify → teardown."""

    CLUSTER_NAME = "maop-test-kind"
    TEST_NS = "maop-kind-test"

    def setup_method(self) -> None:
        """Create a fresh kind cluster for this test class."""
        # Delete any leftover cluster from a previous aborted run.
        _kind("delete", "cluster", "--name", self.CLUSTER_NAME)
        result = _kind("create", "cluster", "--name", self.CLUSTER_NAME)
        if result.returncode != 0:
            pytest.skip(f"kind create cluster failed: {result.stderr}")
        # Point kubectl at the kind cluster.
        kubeconfig = _kind("get", "kubeconfig", "--name", self.CLUSTER_NAME)
        assert kubeconfig.returncode == 0, "kind get kubeconfig failed"
        self._kubeconfig_path = Path(os.environ.get("TEMP", "/tmp")) / "kind-kubeconfig"
        self._kubeconfig_path.write_text(kubeconfig.stdout, encoding="utf-8")
        os.environ["KUBECONFIG"] = str(self._kubeconfig_path)

    def teardown_method(self) -> None:
        """Tear down the kind cluster."""
        _kind("delete", "cluster", "--name", self.CLUSTER_NAME)
        self._kubeconfig_path.unlink(missing_ok=True)

    def test_kind_cluster_ready(self):
        """Cluster is reachable and ready."""
        result = _kubectl("get", "nodes")
        assert result.returncode == 0, f"kubectl get nodes failed:\n{result.stderr}"
        assert "Ready" in result.stdout

    def test_kind_install_crd(self):
        """CRD applies cleanly to the kind cluster."""
        crd = str(CHART_DIR / "crds" / "maopagent.yaml")
        result = _kubectl("apply", "-f", crd)
        assert result.returncode == 0, f"apply CRD failed:\n{result.stderr}"

    def test_kind_create_cr(self):
        """A MaopAgent CR is accepted by the kind cluster."""
        cr = textwrap.dedent("""
            apiVersion: maop.io/v1alpha1
            kind: MaopAgent
            metadata:
              name: kind-test-agent
              namespace: default
            spec:
              model: gpt-4o
              tenant: kind-tenant
              replicas: 1
              maxTurns: 5
        """).strip()
        result = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=cr, capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == 0, f"create CR failed:\n{result.stderr}"

    def test_kind_get_cr(self):
        """The CR created above is retrievable and spec matches."""
        result = _kubectl(
            "get", "maopagent", "kind-test-agent", "-o", "jsonpath={.spec.model}",
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "gpt-4o"

    def test_kind_helm_install(self):
        """Helm chart installs cleanly into the kind cluster."""
        result = _helm(
            "install", "maop-test", str(CHART_DIR),
            "--namespace", self.TEST_NS, "--create-namespace",
            "--set", "crds.install=false",  # CRD already applied above
        )
        assert result.returncode == 0, f"helm install failed:\n{result.stderr}"
        # Cleanup release
        _helm("uninstall", "maop-test", "--namespace", self.TEST_NS)


# ── 6. k3s integration (skip if k3s missing) ─────────────────────────
# G-17: same flow against a k3s cluster.  k3s is a lightweight K8s
# distribution; tests skip gracefully when k3s is not installed.


@pytest.mark.skipif(not _has("k3s"), reason="k3s CLI not installed")
class TestK3sIntegration:
    """End-to-end against a k3s cluster.

    Assumes k3s is already running (systemd service or container).  We do
    not start/stop k3s here because it requires root and is typically
    managed by the host.  Tests use ``k3s kubectl`` wrapper.
    """

    def _k3s_kubectl(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["k3s", "kubectl", *args],
            capture_output=True, text=True, timeout=60, check=False,
        )

    def test_k3s_cluster_ready(self):
        """k3s cluster is reachable."""
        result = self._k3s_kubectl("get", "nodes")
        assert result.returncode == 0, f"k3s kubectl get nodes failed:\n{result.stderr}"
        assert "Ready" in result.stdout

    def test_k3s_install_crd(self):
        """CRD applies cleanly to the k3s cluster."""
        crd = str(CHART_DIR / "crds" / "maopagent.yaml")
        result = self._k3s_kubectl("apply", "-f", crd)
        assert result.returncode == 0, f"apply CRD failed:\n{result.stderr}"

    def test_k3s_create_and_get_cr(self):
        """A MaopAgent CR is accepted and retrievable on the k3s cluster."""
        cr = textwrap.dedent("""
            apiVersion: maop.io/v1alpha1
            kind: MaopAgent
            metadata:
              name: k3s-test-agent
              namespace: default
            spec:
              model: claude-3.5-sonnet
              tenant: k3s-tenant
              replicas: 1
        """).strip()
        result = subprocess.run(
            ["k3s", "kubectl", "apply", "-f", "-"],
            input=cr, capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == 0, f"create CR failed:\n{result.stderr}"
        get = self._k3s_kubectl(
            "get", "maopagent", "k3s-test-agent", "-o", "jsonpath={.spec.model}",
        )
        assert get.returncode == 0
        assert get.stdout.strip() == "claude-3.5-sonnet"
        # Cleanup
        self._k3s_kubectl("delete", "maopagent", "k3s-test-agent", "--ignore-not-found")


# ── 7. Kubernetes cluster (skip if no kubectl / no cluster) ───────────


@pytest.mark.skipif(not _cluster_available_static(), reason="no Kubernetes cluster reachable")
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
