import subprocess
from pathlib import Path

VERIFY_RELEASE_TAG = (
    Path(__file__).parent.parent / ".github" / "scripts" / "verify-release-tag-on-main.sh"
)


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _create_repository(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "--initial-branch=main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("test\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "--message", "Initial commit")


def test_release_tag_on_main_is_accepted(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _create_repository(repository)
    _git(repository, "tag", "v1.2.3")

    result = subprocess.run(
        ["bash", str(VERIFY_RELEASE_TAG), "v1.2.3", "main"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_release_tag_off_main_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _create_repository(repository)
    _git(repository, "switch", "--create", "feature")
    (repository / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repository, "add", "feature.txt")
    _git(repository, "commit", "--message", "Feature commit")
    _git(repository, "tag", "v1.2.3")

    result = subprocess.run(
        ["bash", str(VERIFY_RELEASE_TAG), "v1.2.3", "main"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "is not on main" in result.stderr
