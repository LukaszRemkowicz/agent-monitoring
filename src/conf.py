"""Django-style settings access helpers for the current process."""

from __future__ import annotations

from collections.abc import Mapping
from copy import copy
from functools import lru_cache
from types import ModuleType
from typing import Any, cast

import settings as settings_module

SettingsSource = ModuleType | Mapping[str, Any]


class Settings:
    """Mutable settings object loaded from uppercase module values."""

    def __init__(self, *sources: SettingsSource, **overrides: Any) -> None:
        for source in sources or (settings_module,):
            for name, value in _source_settings(source).items():
                setattr(self, name, copy(value))
        for name, value in overrides.items():
            setattr(self, name, copy(value))

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)

    def copy(self, **updates: Any) -> Settings:
        """Return a shallow settings copy with optional overrides."""

        values = vars(self).copy()
        values.update(updates)
        return Settings(**values)


def _source_settings(source: SettingsSource) -> dict[str, Any]:
    """Return uppercase settings from one module or mapping source."""

    values = vars(source) if isinstance(source, ModuleType) else source
    return {name: value for name, value in values.items() if name.isupper()}


class SettingsProxy:
    """Stable settings object that forwards attribute access to wrapped settings."""

    def __init__(self, wrapped: Settings) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def get_wrapped(self) -> Settings:
        """Return the concrete settings object currently used by the proxy."""

        return self._wrapped

    def set_wrapped(self, wrapped: Settings) -> None:
        """Replace the concrete settings object used by the proxy."""

        self._wrapped = wrapped


@lru_cache(maxsize=1)
def _get_settings() -> Settings:
    """Return a cached settings instance for process-wide reuse."""

    return Settings()


_settings_proxy = SettingsProxy(_get_settings())


def get_settings() -> Settings:
    """Return the concrete settings object currently used by the proxy."""

    return _settings_proxy.get_wrapped()


def set_settings(settings: Settings) -> None:
    """Replace the concrete settings object used by the process-wide proxy."""

    _settings_proxy.set_wrapped(settings)


settings: Settings = cast(Settings, _settings_proxy)
