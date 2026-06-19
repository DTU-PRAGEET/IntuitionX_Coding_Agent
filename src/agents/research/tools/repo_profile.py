"""Repo profiler — detect Java/Gradle vs Python layout."""

from __future__ import annotations

from pathlib import Path

from src.core.state import RepoProfile


def profile_repository(repo_path: str) -> RepoProfile:
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
