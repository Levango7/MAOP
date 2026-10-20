"""Hatch build hook: make repository assets wheel/sdist friendly.

``force-include`` hard-fails when a source path is missing. Two builds
would otherwise break:

* a source install (``pip install "...#subdirectory=py"``) where the
  generated ``dashboard/dist-enterprise`` has not been built yet;
* a wheel built from the sdist, where ``../config`` no longer exists
  because the assets were already copied into ``maop/_assets``.

Missing optional assets are skipped with a warning; a missing config is
fatal unless the packaged copy is already present.
"""

from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class OptionalAssetsHook(BuildHookInterface):
    PLUGIN_NAME = "optional-assets"

    def initialize(self, version, build_data):
        config = self.build_config
        for mapping in (config.force_include, config.build_force_include):
            for source in list(mapping):
                if Path(source).exists():
                    continue
                destination = Path(self.root) / mapping[source]
                # The source is gone (wheel built from an sdist) but the
                # assets already live inside the package tree; dropping the
                # entry avoids a second, failing copy of the same files.
                mapping.pop(source)
                if destination.exists():
                    continue
                if "config" in Path(source).parts:
                    raise FileNotFoundError(
                        f"required build asset missing: {source}"
                    )
                self.app.display_warning(
                    f"skipping missing optional asset: {source}"
                )
