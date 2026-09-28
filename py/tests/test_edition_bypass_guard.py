"""2026-09-28 edition bypass closure tests.

Prior behavior: ``set_edition(ENTERPRISE)`` / ``set_feature_override()`` were
only guarded in production (``MAOP_ENV=production``, M-2 fix), so a default
deployment (``MAOP_ENV`` unset → development) could unlock every enterprise
feature with one in-process call and no license. These tests lock the new
behavior:

  - outside a test runner, ENTERPRISE activation requires a valid license
  - the refused call must not flip the global edition state
  - under pytest the exemption still holds (license-free fixtures keep working)
  - production keeps refusing (M-2 regression guard)
  - ``set_feature_override`` follows the same rule
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import maop.config.edition as edition_mod
from maop.config.edition import (
    Edition,
    FeatureFlag,
    get_edition,
    has_feature,
    reset_edition,
    set_edition,
    set_feature_override,
)

_enterprise_pkg_installed = edition_mod._is_enterprise_package_installed()


@pytest.fixture(autouse=True)
def _reset_edition_state():
    """每个测试前后重置 edition 全局状态，避免测试间相互污染。"""
    reset_edition()
    yield
    reset_edition()


@pytest.fixture(autouse=True)
def _no_license(monkeypatch):
    """隔离本机 license：拒绝路径必须确定性失败。

    两处都要打桩，缺一不可——``LicenseValidator()`` 在**构造期**就会读公钥
    文件（``__init__`` → ``_load_public_key``），同进程若有别的测试改过
    ``_PUBLIC_KEY_PATH``（历史：test_enterprise_license.py 的模块级全局赋值），
    构造就会先抛 ``LicenseError``，根本走不到被断言的拒绝逻辑。

    企业包未安装的环境（如 MAOP CI）走 ImportError 分支，无需打桩。
    """
    monkeypatch.delenv("MAOP_LICENSE_KEY", raising=False)
    if edition_mod._is_enterprise_package_installed():
        monkeypatch.setattr(
            "maop.enterprise.license.LicenseValidator.__init__",
            lambda self, *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "maop.enterprise.license.LicenseValidator.validate_from_env",
            lambda self: None,
        )


class TestInTestRunnerDetection:
    def test_pytest_detected_via_sys_modules(self):
        # 我们就跑在 pytest 里——sys.modules 探测必须命中
        assert edition_mod._in_test_runner() is True

    def test_env_var_detected(self, monkeypatch):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y (call)")
        assert edition_mod._in_test_runner() is True


class TestSetEditionBypassClosed:
    def test_enterprise_refused_outside_test_runner(self, monkeypatch):
        """非测试运行器环境：无 license 的 set_edition(ENTERPRISE) 必须被拒。

        这是 2026-09-28 旁路封堵的核心断言——默认部署（MAOP_ENV 未设，
        development）此前一行调用即可解锁全部企业功能。
        """
        monkeypatch.setattr(edition_mod, "_in_test_runner", lambda: False)
        monkeypatch.delenv("MAOP_LICENSE_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SECURITY"):
            set_edition(Edition.ENTERPRISE)

    def test_refused_call_does_not_flip_edition_state(self, monkeypatch):
        """拒绝后全局 edition 不得被改成 enterprise（状态不得泄漏）。"""
        monkeypatch.setattr(edition_mod, "_in_test_runner", lambda: False)
        monkeypatch.delenv("MAOP_LICENSE_KEY", raising=False)
        with pytest.raises(RuntimeError):
            set_edition(Edition.ENTERPRISE)
        assert get_edition() is Edition.PERSONAL

    def test_refusal_message_mentions_license_activation(self, monkeypatch):
        """报错必须指明正确的激活方式（MAOP_LICENSE_KEY），便于运维定位。"""
        monkeypatch.setattr(edition_mod, "_in_test_runner", lambda: False)
        monkeypatch.delenv("MAOP_LICENSE_KEY", raising=False)
        with pytest.raises(RuntimeError, match="MAOP_LICENSE_KEY"):
            set_edition(Edition.ENTERPRISE)

    def test_enterprise_refused_in_production(self, monkeypatch):
        """M-2 回归守卫：production 下同样拒绝（且不受测试豁免影响）。"""
        monkeypatch.setattr(edition_mod, "_in_test_runner", lambda: False)
        monkeypatch.setenv("MAOP_ENV", "production")
        monkeypatch.delenv("MAOP_LICENSE_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SECURITY"):
            set_edition(Edition.ENTERPRISE)

    def test_personal_still_allowed_outside_test_runner(self, monkeypatch):
        """PERSONAL 切换不受影响——守卫只针对 ENTERPRISE 激活。"""
        monkeypatch.setattr(edition_mod, "_in_test_runner", lambda: False)
        set_edition(Edition.PERSONAL)
        assert get_edition() is Edition.PERSONAL

    @pytest.mark.skipif(
        not _enterprise_pkg_installed,
        reason="maop.enterprise not installed in this environment",
    )
    def test_enterprise_allowed_with_valid_license(self, monkeypatch):
        """持有有效 license 时非测试环境也允许激活（正常授权路径）。"""
        monkeypatch.setattr(edition_mod, "_in_test_runner", lambda: False)
        fake_info = SimpleNamespace(customer="acme", expires_at=None)
        with patch("maop.enterprise.license.LicenseValidator") as mock_validator:
            mock_validator.return_value.validate_from_env.return_value = fake_info
            set_edition(Edition.ENTERPRISE)
        assert get_edition() is Edition.ENTERPRISE

    def test_enterprise_still_allowed_under_pytest_without_license(self):
        """pytest 豁免依旧成立——MAOS conftest 的 enterprise_edition 夹具依赖它。"""
        set_edition(Edition.ENTERPRISE)
        assert get_edition() is Edition.ENTERPRISE


class TestSetFeatureOverrideGuard:
    def test_override_refused_outside_test_runner(self, monkeypatch):
        monkeypatch.setattr(edition_mod, "_in_test_runner", lambda: False)
        with pytest.raises(RuntimeError, match="SECURITY"):
            set_feature_override(FeatureFlag.RBAC, True)
        assert has_feature(FeatureFlag.RBAC) is False

    def test_override_refused_in_production(self, monkeypatch):
        """M-2 回归守卫：production 下维持原有拒绝。"""
        monkeypatch.setenv("MAOP_ENV", "production")
        with pytest.raises(RuntimeError, match="production"):
            set_feature_override(FeatureFlag.RBAC, True)

    def test_override_allowed_under_pytest(self):
        set_feature_override(FeatureFlag.RBAC, True)
        assert has_feature(FeatureFlag.RBAC) is True
