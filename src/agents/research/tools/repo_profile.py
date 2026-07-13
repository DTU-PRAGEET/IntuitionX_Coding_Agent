"""Repo profiler — detect Java/Gradle vs Python layout."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import tool

from src.core.state import RepoProfile


def bootstrap_repo_context(repo_path: str, *, max_entries: int = 12) -> dict[str, object]:
    """Verified repo facts for research prompts — prevents false repo_path_not_found."""
    root = Path(repo_path).resolve()
    if not root.exists():
        return {
            "repo_path": str(root),
            "exists": False,
            "is_git": False,
            "top_level_entries": [],
            "file_count_estimate": 0,
        }

    top_level: list[str] = []
    file_count = 0
    try:
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if child.name == ".git":
                continue
            top_level.append(child.name + ("/" if child.is_dir() else ""))
            if len(top_level) >= max_entries:
                break
        for _ in root.rglob("*"):
            file_count += 1
            if file_count >= 5000:
                break
    except OSError:
        pass

    return {
        "repo_path": str(root),
        "exists": True,
        "is_git": (root / ".git").exists(),
        "top_level_entries": top_level,
        "file_count_estimate": file_count,
    }


def profile_repository(repo_path: str) -> RepoProfile:
    """Profile Repository for this module."""
    root = Path(repo_path).resolve()
    if not root.exists():
        return RepoProfile(
            repo_type="unknown",
            root_path=str(root),
            modules=[],
            package_roots=[],
        )

    modules: list[str] = []
    package_roots: list[str] = []
    repo_type: str = "unknown"

    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        repo_type = "java_gradle"
        settings = root / "settings.gradle"
        if settings.exists():
            for line in settings.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("include"):
                    for part in line.replace("include", "").replace("'", "").split(","):
                        name = part.strip().strip("'\"")
                        if name:
                            modules.append(name.replace(":", "/").strip("/"))
        if not modules:
            modules = ["."]
        package_roots = ["src/main/java", "src/test/java"]

    elif (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        repo_type = "python"
        modules = ["."]
        for candidate in ("src", "."):
            p = root / candidate
            if p.is_dir():
                package_roots.append(candidate)

    elif (root / "requirements.txt").exists():
        repo_type = "python"
        modules = ["."]
        package_roots = ["."]

    return RepoProfile(
        repo_type=repo_type,  # type: ignore
        root_path=str(root),
        modules=modules,
        package_roots=package_roots,
    )


def make_repo_profile_tool(repo_path: str):
    """Factory — bind repo path for repo_profile tool."""

    @tool
    def repo_profile() -> str:
        """Return repo layout JSON (type, modules, package roots). No arguments."""
        profile = profile_repository(repo_path)
        return json.dumps(dict(profile), indent=2)

    return repo_profile
