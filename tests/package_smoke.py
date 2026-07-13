"""Smoke-test an installed phosphor-eda distribution through its public interfaces."""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import json
import subprocess


def main() -> None:
    """Verify package metadata, bundled resources, and the installed CLI."""
    import phosphor_eda

    installed_version = importlib.metadata.version("phosphor-eda")
    assert phosphor_eda.__version__ == installed_version, (
        f"module version {phosphor_eda.__version__!r} does not match "
        f"distribution version {installed_version!r}"
    )

    package = importlib.resources.files("phosphor_eda")
    expected_files = (
        "py.typed",
        "skill.md",
        "geometry/fonts/Inter-Regular.ttf",
        "render/profiles/design.json",
        "render/profiles/documentation.json",
        "render/profiles/print.json",
        "render/profiles/realistic.json",
    )
    for relative_path in expected_files:
        resource = package.joinpath(relative_path)
        assert resource.is_file(), f"installed distribution is missing {relative_path}"

    for profile_name in ("design", "documentation", "print", "realistic"):
        profile = package.joinpath(f"render/profiles/{profile_name}.json")
        parsed: object = json.loads(profile.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict), f"render profile {profile_name!r} is not a JSON object"

    completed = subprocess.run(
        ["phosphor-eda", "--help"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Usage:" in completed.stdout


if __name__ == "__main__":
    main()
