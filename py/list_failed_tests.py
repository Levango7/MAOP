"""List all failed tests from the R4 test run."""
import subprocess
import sys
import re

cmd = [
    sys.executable, "-m", "pytest", "tests/", "-n", "4", "-q",
    "-p", "no:cacheprovider", "--no-cov", "-o", "addopts=",
    "--tb=line",
    "--deselect", "tests/test_edition_switch_guard.py::TestEditionSwitchEnterpriseGuard::test_403_error_message_contains_authorization_hint",
    "--deselect", "tests/test_module_integrity.py::TestModuleIntegrity::test_intact_tree_verifies",
    "--deselect", "tests/test_module_integrity.py::TestModuleIntegrity::test_tampered_module_detected",
    "--deselect", "tests/test_enterprise_integration.py::TestSAMLIntegration::test_saml_handler_parses_real_signed_response",
]

result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=r"F:\Nexus\MAOP\py")

# Extract FAILED lines
failed_tests = []
for line in result.stdout.splitlines():
    if line.startswith("FAILED"):
        failed_tests.append(line)

print(f"Total failed: {len(failed_tests)}")
for t in failed_tests:
    print(t)

# Also print the summary line
for line in result.stdout.splitlines():
    if "passed" in line and "failed" in line:
        print(f"\nSummary: {line}")
        break