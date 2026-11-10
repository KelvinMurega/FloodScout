"""Reading a run's config.

Configs are TOML, read with the standard library's tomllib, so the pipeline
needs no package the app does not already have.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

MODELS = ("hazard", "susceptibility")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class RunConfig:
    path: Path
    model: str
    settings: dict

    @property
    def name(self) -> str:
        return self.path.stem

    def section(self, name: str) -> dict:
        try:
            return self.settings[name]
        except KeyError:
            raise ConfigError(f"{self.path.name} has no [{name}] section.") from None

    def value(self, section: str, key: str):
        try:
            return self.section(section)[key]
        except KeyError:
            raise ConfigError(f"{self.path.name} has no {key} in [{section}].") from None


def load(path: Path) -> RunConfig:
    try:
        settings = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid TOML: {exc}") from exc
    model = settings.get("model")
    if model not in MODELS:
        raise ConfigError(f"{path.name}: model must be one of {', '.join(MODELS)}, not {model!r}.")
    return RunConfig(path=path, model=model, settings=settings)
