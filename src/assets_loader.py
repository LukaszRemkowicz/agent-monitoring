"""Prompt asset loading helpers."""

from __future__ import annotations

from pathlib import Path


class PromptAssetLoader:
    """Load markdown prompt assets from one configured directory."""

    def __init__(self, asset_dir: Path | None = None) -> None:
        self.asset_dir = asset_dir or Path(__file__).resolve().parent / "prompt_assets"

    def load_text(self, name: str) -> str:
        """Return one markdown prompt asset."""

        return (self.asset_dir / name).read_text(encoding="utf-8").strip()

    def load_markdown_bullets(self, name: str) -> list[str]:
        """Return top-level markdown bullet items from one prompt asset."""

        text: str = self.load_text(name)
        return [
            line.removeprefix("- ").strip() for line in text.splitlines() if line.startswith("- ")
        ]

    def load_markdown_mapping(self, name: str) -> dict[str, str]:
        """Return `field: description` bullet items from one prompt asset."""

        mapping: dict[str, str] = {}
        for item in self.load_markdown_bullets(name):
            key, separator, value = item.partition(":")
            if not separator:
                raise ValueError(f"Prompt asset {name} contains invalid mapping item: {item}")
            mapping[key.strip()] = value.strip()
        return mapping


loader = PromptAssetLoader()


def load_text(name: str) -> str:
    """Return one markdown prompt asset from the default prompt asset dir."""

    return loader.load_text(name)


def load_markdown_bullets(name: str) -> list[str]:
    """Return top-level markdown bullet items from the default prompt asset dir."""

    return loader.load_markdown_bullets(name)


def load_markdown_mapping(name: str) -> dict[str, str]:
    """Return mapping bullet items from the default prompt asset dir."""

    return loader.load_markdown_mapping(name)
