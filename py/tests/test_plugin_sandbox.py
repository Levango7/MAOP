"""Tests for PluginSandbox security isolation (P0 fix)."""

from __future__ import annotations

import json
import time

import pytest

from maop.core.agent.plugins_hooks.plugin import (
    PluginManager,
    PluginSandbox,
    SandboxViolation,
)


class TestPluginSandboxPathValidation:
    def test_valid_path_inside_plugins(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "my_plugin" / "main.py"
        target.parent.mkdir(parents=True)
        target.write_text("pass", encoding="utf-8")
        resolved = sandbox.validate_path(target)
        assert str(resolved).startswith(str(plugins_dir.resolve()))

    def test_path_traversal_blocked(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        outside = tmp_path / "etc" / "passwd"
        outside.parent.mkdir(parents=True)
        outside.write_text("root:x:0:0", encoding="utf-8")
        with pytest.raises(SandboxViolation, match="Path traversal"):
            sandbox.validate_path(outside)

    def test_symlink_escape_blocked(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        link = plugins_dir / "evil_link.py"
        try:
            link.symlink_to(tmp_path / "outside.txt")
        except OSError:
            pytest.skip(
                "symlinks not supported on this platform/Environment "
                "(Windows requires admin privileges or Developer Mode enabled)"
            )
        with pytest.raises(SandboxViolation, match="Path traversal"):
            sandbox.validate_path(link)


class TestPluginSandboxChecksum:
    def test_valid_checksum(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "main.py"
        target.write_text("pass", encoding="utf-8")
        import hashlib
        expected = hashlib.sha256(target.read_bytes()).hexdigest()
        sandbox.verify_checksum(target, expected)

    def test_invalid_checksum(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "main.py"
        target.write_text("pass", encoding="utf-8")
        with pytest.raises(SandboxViolation, match="Checksum mismatch"):
            sandbox.verify_checksum(target, "0000000000000000")

    def test_empty_checksum_skips_verification(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "main.py"
        target.write_text("pass", encoding="utf-8")
        sandbox.verify_checksum(target, "")


class TestPluginSandboxRestrictedBuiltins:
    def test_exec_blocked(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "evil.py"
        target.write_text("def MAOP_plugin_init(cfg): exec('1+1')\n", encoding="utf-8")
        module, spec = sandbox.create_restricted_module("test_evil", target)
        sandbox.exec_module(module, spec)
        with pytest.raises((SandboxViolation, NameError)):
            module.MAOP_plugin_init({})

    def test_eval_blocked(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "evil.py"
        target.write_text("def MAOP_plugin_init(cfg): eval('1+1')\n", encoding="utf-8")
        module, spec = sandbox.create_restricted_module("test_evil2", target)
        sandbox.exec_module(module, spec)
        with pytest.raises((SandboxViolation, NameError)):
            module.MAOP_plugin_init({})

    def test_open_blocked(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "evil.py"
        target.write_text("def MAOP_plugin_init(cfg): open('/etc/passwd')\n", encoding="utf-8")
        module, spec = sandbox.create_restricted_module("test_evil3", target)
        sandbox.exec_module(module, spec)
        with pytest.raises(SandboxViolation, match="open"):
            module.MAOP_plugin_init({})


class TestPluginSandboxImportGuard:
    def test_allowed_import_json(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "good.py"
        target.write_text(
            "import json\ndef MAOP_plugin_init(cfg): json.dumps({'ok': True})\n",
            encoding="utf-8",
        )
        module, spec = sandbox.create_restricted_module("test_good", target)
        sandbox.exec_module(module, spec)
        module.MAOP_plugin_init({})

    def test_blocked_import_os(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "evil.py"
        target.write_text("import os\ndef MAOP_plugin_init(cfg): pass\n", encoding="utf-8")
        module, spec = sandbox.create_restricted_module("test_os", target)
        with pytest.raises(SandboxViolation, match="Import blocked"):
            sandbox.exec_module(module, spec)

    def test_blocked_import_subprocess(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "evil.py"
        target.write_text("import subprocess\ndef MAOP_plugin_init(cfg): pass\n", encoding="utf-8")
        module, spec = sandbox.create_restricted_module("test_sub", target)
        with pytest.raises(SandboxViolation, match="Import blocked"):
            sandbox.exec_module(module, spec)

    def test_blocked_import_sys(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir)
        target = plugins_dir / "evil.py"
        target.write_text("import sys\ndef MAOP_plugin_init(cfg): pass\n", encoding="utf-8")
        module, spec = sandbox.create_restricted_module("test_sys", target)
        with pytest.raises(SandboxViolation, match="Import blocked"):
            sandbox.exec_module(module, spec)

    def test_custom_allowed_import(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir, allowed_imports=frozenset({"os"}))
        target = plugins_dir / "custom.py"
        target.write_text("import os\ndef MAOP_plugin_init(cfg): pass\n", encoding="utf-8")
        module, spec = sandbox.create_restricted_module("test_custom", target)
        sandbox.exec_module(module, spec)


class TestPluginSandboxTimeout:
    def test_init_completes_within_timeout(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir, timeout_seconds=5.0)
        sandbox.run_init_with_timeout(lambda cfg: None, {}, timeout=5.0)

    def test_init_exceeds_timeout(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir, timeout_seconds=1.0)

        def slow_init(cfg: dict) -> None:
            time.sleep(10)

        with pytest.raises(SandboxViolation, match="timeout"):
            sandbox.run_init_with_timeout(slow_init, {}, timeout=1.0)

    def test_zero_timeout_no_limit(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        sandbox = PluginSandbox(plugins_dir, timeout_seconds=0)
        sandbox.run_init_with_timeout(lambda cfg: None, {}, timeout=0)


class TestPluginManagerSandboxIntegration:
    def test_sandbox_enabled_by_default(self, tmp_path):
        mgr = PluginManager(root_dir=str(tmp_path))
        assert mgr._sandbox_enabled is True

    def test_sandbox_can_be_disabled(self, tmp_path):
        mgr = PluginManager(root_dir=str(tmp_path), sandbox_enabled=False)
        assert mgr._sandbox_enabled is False

    def test_load_with_sandbox_allows_safe_plugin(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "safe"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "safe", "version": "1.0.0"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "main.py").write_text(
            "import json\ndef MAOP_plugin_init(cfg): pass\n",
            encoding="utf-8",
        )
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state.value == "loaded"

    def test_load_with_sandbox_blocks_os_import(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "evil"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "evil", "version": "1.0.0"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "main.py").write_text(
            "import os\ndef MAOP_plugin_init(cfg): pass\n",
            encoding="utf-8",
        )
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state.value == "errored"

    def test_load_with_checksum_verification(self, tmp_path):
        import hashlib
        plugin_dir = tmp_path / "plugins" / "checked"
        plugin_dir.mkdir(parents=True)
        main_py = plugin_dir / "main.py"
        main_py.write_text("def MAOP_plugin_init(cfg): pass\n", encoding="utf-8")
        checksum = hashlib.sha256(main_py.read_bytes()).hexdigest()
        manifest = {"name": "checked", "version": "1.0.0", "checksum": checksum}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state.value == "loaded"

    def test_load_with_bad_checksum_fails(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "tampered"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "main.py").write_text("def MAOP_plugin_init(cfg): pass\n", encoding="utf-8")
        manifest = {"name": "tampered", "version": "1.0.0", "checksum": "badhash"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state.value == "errored"

    def test_load_sandbox_disabled_allows_os(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "unrestricted"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "unrestricted", "version": "1.0.0"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "main.py").write_text(
            "import os\ndef MAOP_plugin_init(cfg): pass\n",
            encoding="utf-8",
        )
        mgr = PluginManager(root_dir=str(tmp_path), sandbox_enabled=False)
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state.value == "loaded"

    def test_manifest_allowed_imports_extend_default(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "extended"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "extended", "version": "1.0.0", "allowed_imports": ["os"]}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "main.py").write_text(
            "import os\ndef MAOP_plugin_init(cfg): pass\n",
            encoding="utf-8",
        )
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state.value == "loaded"

    def test_start_with_sandbox_timeout(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "slow"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "slow", "version": "1.0.0", "timeout_seconds": 1.0}
        (plugin_dir / "MAOP-plugin.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "main.py").write_text(
            "import time\ndef MAOP_plugin_init(cfg): time.sleep(10)\n",
            encoding="utf-8",
        )
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        mgr.load(pid)
        info = mgr.start(pid)
        assert info.state.value == "errored"



# ── Strict-default checksum enforcement (t08) ─────────────────────


class TestPluginChecksumDefault:
    """Verify that checksum is mandatory by default (fail-closed)."""

    def test_load_without_checksum_rejected_by_default(self, tmp_path, monkeypatch):
        """Plugins without checksum are rejected when env var is unset."""
        monkeypatch.delenv("MAOP_PLUGIN_STRICT_CHECKSUM", raising=False)
        plugin_dir = tmp_path / "plugins" / "nocsum"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "nocsum", "version": "1.0.0"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (plugin_dir / "main.py").write_text(
            "def MAOP_plugin_init(cfg): pass\n", encoding="utf-8"
        )
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state.value == "errored"
        assert "checksum is mandatory" in info.error

    def test_load_without_checksum_allowed_when_strict_disabled(self, tmp_path, monkeypatch):
        """Plugins without checksum are allowed when MAOP_PLUGIN_STRICT_CHECKSUM=0."""
        monkeypatch.setenv("MAOP_PLUGIN_STRICT_CHECKSUM", "0")
        plugin_dir = tmp_path / "plugins" / "nocsum"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "nocsum", "version": "1.0.0"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (plugin_dir / "main.py").write_text(
            "def MAOP_plugin_init(cfg): pass\n", encoding="utf-8"
        )
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state.value == "loaded"

    def test_strict_default_explicit_one_also_rejects(self, tmp_path, monkeypatch):
        """Explicit MAOP_PLUGIN_STRICT_CHECKSUM=1 also rejects (parity with unset)."""
        monkeypatch.setenv("MAOP_PLUGIN_STRICT_CHECKSUM", "1")
        plugin_dir = tmp_path / "plugins" / "nocsum2"
        plugin_dir.mkdir(parents=True)
        manifest = {"name": "nocsum2", "version": "1.0.0"}
        (plugin_dir / "MAOP-plugin.yaml").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (plugin_dir / "main.py").write_text(
            "def MAOP_plugin_init(cfg): pass\n", encoding="utf-8"
        )
        mgr = PluginManager(root_dir=str(tmp_path))
        mgr.discover()
        pid = mgr.list_plugins()[0].id
        info = mgr.load(pid)
        assert info.state.value == "errored"
        assert "checksum is mandatory" in info.error


# ── Dunder escape chain hardening (P1-2) ──────────────────────────────


class TestPluginSandboxDunderEscape:
    """Verify that the classic ``__subclasses__`` escape chain and its
    variants are blocked by the static AST scan + builtins whitelist."""

    @staticmethod
    def _make_sandbox(tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        return PluginSandbox(plugins_dir), plugins_dir

    def test_subclasses_chain_blocked_at_parse_time(self, tmp_path):
        """``().__class__.__bases__[0].__subclasses__()`` must be rejected
        before execution — the canonical sandbox escape."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg):\n"
            "    subs = ().__class__.__bases__[0].__subclasses__()\n",
            encoding="utf-8",
        )
        with pytest.raises(SandboxViolation, match="sandbox escape primitive"):
            sandbox.create_restricted_module("evil_sub", target)

    def test_class_attribute_blocked(self, tmp_path):
        """Direct ``obj.__class__`` access is blocked."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg):\n"
            "    cls = ().__class__\n",
            encoding="utf-8",
        )
        with pytest.raises(SandboxViolation, match="__class__"):
            sandbox.create_restricted_module("evil_cls", target)

    def test_globals_attribute_blocked(self, tmp_path):
        """``fn.__globals__`` access is blocked (leaks real builtins)."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def _helper(): pass\n"
            "def MAOP_plugin_init(cfg):\n"
            "    g = _helper.__globals__\n",
            encoding="utf-8",
        )
        with pytest.raises(SandboxViolation, match="__globals__"):
            sandbox.create_restricted_module("evil_g", target)

    def test_code_object_blocked(self, tmp_path):
        """``fn.__code__`` access is blocked (enables bytecode tricks)."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def _helper(): pass\n"
            "def MAOP_plugin_init(cfg):\n"
            "    c = _helper.__code__\n",
            encoding="utf-8",
        )
        with pytest.raises(SandboxViolation, match="__code__"):
            sandbox.create_restricted_module("evil_code", target)

    def test_dict_attribute_blocked(self, tmp_path):
        """``obj.__dict__`` access is blocked (namespace leakage)."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "class _X: pass\n"
            "def MAOP_plugin_init(cfg):\n"
            "    d = _X().__dict__\n",
            encoding="utf-8",
        )
        with pytest.raises(SandboxViolation, match="__dict__"):
            sandbox.create_restricted_module("evil_dict", target)

    def test_mro_access_blocked(self, tmp_path):
        """``cls.__mro__`` access is blocked."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg):\n"
            "    m = str.__mro__\n",
            encoding="utf-8",
        )
        with pytest.raises(SandboxViolation, match="__mro__"):
            sandbox.create_restricted_module("evil_mro", target)

    def test_reduce_blocked(self, tmp_path):
        """``obj.__reduce__`` access is blocked (pickle code execution)."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg):\n"
            "    r = ().__reduce__()\n",
            encoding="utf-8",
        )
        with pytest.raises(SandboxViolation, match="__reduce__"):
            sandbox.create_restricted_module("evil_reduce", target)


class TestPluginSandboxDangerousBuiltins:
    """Verify that dangerous builtins are blocked at runtime even when
    the source passes the static AST scan (no dunder attributes)."""

    @staticmethod
    def _make_sandbox(tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        return PluginSandbox(plugins_dir), plugins_dir

    def test_globals_builtin_blocked(self, tmp_path):
        """``globals()`` must raise — it can recover the real builtins."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg): globals()\n", encoding="utf-8"
        )
        module, spec = sandbox.create_restricted_module("evil_g", target)
        sandbox.exec_module(module, spec)
        with pytest.raises(SandboxViolation, match="globals"):
            module.MAOP_plugin_init({})

    def test_locals_builtin_blocked(self, tmp_path):
        """``locals()`` must raise — namespace introspection."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg): locals()\n", encoding="utf-8"
        )
        module, spec = sandbox.create_restricted_module("evil_l", target)
        sandbox.exec_module(module, spec)
        with pytest.raises(SandboxViolation, match="locals"):
            module.MAOP_plugin_init({})

    def test_setattr_builtin_blocked(self, tmp_path):
        """``setattr()`` must raise — enables attribute injection."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg): setattr(cfg, 'x', 1)\n",
            encoding="utf-8",
        )
        module, spec = sandbox.create_restricted_module("evil_sa", target)
        sandbox.exec_module(module, spec)
        with pytest.raises(SandboxViolation, match="setattr"):
            module.MAOP_plugin_init({})

    def test_hasattr_builtin_blocked(self, tmp_path):
        """``hasattr()`` must raise — attribute probing."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg): hasattr(cfg, 'x')\n",
            encoding="utf-8",
        )
        module, spec = sandbox.create_restricted_module("evil_ha", target)
        sandbox.exec_module(module, spec)
        with pytest.raises(SandboxViolation, match="hasattr"):
            module.MAOP_plugin_init({})

    def test_type_builtin_blocked(self, tmp_path):
        """``type()`` must raise — enables inheritance graph walks."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg): type(cfg)\n", encoding="utf-8"
        )
        module, spec = sandbox.create_restricted_module("evil_t", target)
        sandbox.exec_module(module, spec)
        with pytest.raises(SandboxViolation, match="type"):
            module.MAOP_plugin_init({})

    def test_object_builtin_blocked(self, tmp_path):
        """``object`` must raise — base of the inheritance graph."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg): object.__subclasses__()\n",
            encoding="utf-8",
        )
        # Blocked at AST scan time (``__subclasses__``) before runtime.
        with pytest.raises(SandboxViolation, match="__subclasses__"):
            sandbox.create_restricted_module("evil_obj", target)

    def test_vars_builtin_blocked(self, tmp_path):
        """``vars()`` must raise — namespace introspection."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg): vars()\n", encoding="utf-8"
        )
        module, spec = sandbox.create_restricted_module("evil_v", target)
        sandbox.exec_module(module, spec)
        with pytest.raises(SandboxViolation, match="vars"):
            module.MAOP_plugin_init({})

    def test_dir_builtin_blocked(self, tmp_path):
        """``dir()`` must raise — attribute enumeration."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "evil.py"
        target.write_text(
            "def MAOP_plugin_init(cfg): dir()\n", encoding="utf-8"
        )
        module, spec = sandbox.create_restricted_module("evil_d", target)
        sandbox.exec_module(module, spec)
        with pytest.raises(SandboxViolation, match="dir"):
            module.MAOP_plugin_init({})


class TestPluginSandboxWhitelistPreservesSafeCode:
    """Verify that legitimate plugin code (no dunder, no dangerous
    builtins) still loads and runs after the hardening."""

    def test_safe_plugin_loads_and_runs(self, tmp_path):
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "good.py"
        target.write_text(
            "import json\n"
            "def MAOP_plugin_init(cfg):\n"
            "    data = json.dumps({'ok': True, 'n': len([1, 2, 3])})\n"
            "    assert sorted([3, 1, 2]) == [1, 2, 3]\n"
            "    assert sum([1, 2, 3]) == 6\n"
            "    return data\n",
            encoding="utf-8",
        )
        module, spec = sandbox.create_restricted_module("good", target)
        sandbox.exec_module(module, spec)
        result = module.MAOP_plugin_init({})
        assert '"ok": true' in result

    def test_safe_exceptions_available(self, tmp_path):
        """Plugins can raise and catch standard exceptions."""
        sandbox, plugins_dir = self._make_sandbox(tmp_path)
        target = plugins_dir / "good.py"
        target.write_text(
            "def MAOP_plugin_init(cfg):\n"
            "    try:\n"
            "        raise ValueError('boom')\n"
            "    except ValueError as e:\n"
            "        return str(e)\n",
            encoding="utf-8",
        )
        module, spec = sandbox.create_restricted_module("good_exc", target)
        sandbox.exec_module(module, spec)
        assert module.MAOP_plugin_init({}) == "boom"

    @staticmethod
    def _make_sandbox(tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        return PluginSandbox(plugins_dir), plugins_dir
