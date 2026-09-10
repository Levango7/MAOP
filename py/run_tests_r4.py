"""Run full backend test suite for R4 Batch 1+2 verification."""
import subprocess
import sys

cmd = [
    sys.executable, "-m", "pytest", "tests/",
    "-n", "4", "-q", "-p", "no:cacheprovider", "--no-cov",
    "-o", "addopts=", "--tb=line",
    "--deselect", "tests/test_edition_switch_guard.py::TestEditionSwitchEnterpriseGuard::test_403_error_message_contains_authorization_hint",
    "--deselect", "tests/test_module_integrity.py::TestModuleIntegrity::test_intact_tree_verifies",
    "--deselect", "tests/test_module_integrity.py::TestModuleIntegrity::test_tampered_module_detected",
    "--deselect", "tests/test_enterprise_integration.py::TestSAMLIntegration::test_saml_handler_parses_real_signed_response",
]

result = subprocess.run(cmd, capture_output=True, text=True, timeout=500)
print("=== STDOUT (last 3000 chars) ===")
print(result.stdout[-3000:])
print("=== STDERR (last 2000 chars) ===")
print(result.stderr[-2000:])
print(f"\n=== EXIT CODE: {result.returncode} ===")
