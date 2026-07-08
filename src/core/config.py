"""Environment-based configuration loader."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env.local first, then .env
_root = Path(__file__).resolve().parents[2]
_env_local = _root / ".env.local"
_env_file = _root / ".env"


def load_project_env() -> None:
    """Load `.env.local` / `.env` into os.environ. Safe to call from CLI entry points."""
    if _env_local.exists():
        load_dotenv(_env_local, override=True)
    if _env_file.exists():
        load_dotenv(_env_file, override=False)


load_project_env()


@dataclass(frozen=True)
class Settings:
    """Frozen env-backed config singleton (`settings`) — LLM, Jira, Redis, Cursor, git.
    Loaded from `.env.local` / `.env` at import; read across agents, API, and integrations."""
    llm_provider: str = os.getenv("LLM_PROVIDER", "local")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")
    google_model: str = os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    sprinklr_llm_base_url: str = os.getenv(
        "SPRINKLR_LLM_BASE_URL",
        "http://qa6-intuitionx-llm-router-v2.sprinklr.com",
    )
    sprinklr_llm_model: str = os.getenv("SPRINKLR_LLM_MODEL", "gpt-5.2-2025-12-11")
    sprinklr_llm_client_identifier: str = os.getenv(
        "SPRINKLR_LLM_CLIENT_IDENTIFIER", "backend-platform-dev"
    )
    sprinklr_llm_partner_id: str = os.getenv("SPRINKLR_LLM_PARTNER_ID", "66006006")
    sprinklr_llm_tracking_feature: str = os.getenv(
        "SPRINKLR_LLM_TRACKING_FEATURE", "SPRINKLR_JIRA_SOLVER"
    )
    token_budget: int = int(os.getenv("TOKEN_BUDGET", "150000"))

    jira_server_url: str = os.getenv("JIRA_SERVER_URL", "")
    jira_email: str = os.getenv("JIRA_EMAIL", "")
    jira_api_token: str = os.getenv("JIRA_API_TOKEN", "")
    jira_vulnerability_category_field: str = os.getenv("JIRA_VULNERABILITY_CATEGORY_FIELD", "")

    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    mongo_url: str = os.getenv("MONGO_URL", "mongodb://localhost:27017")

    gitlab_url: str = os.getenv("GITLAB_URL", "")
    gitlab_token: str = os.getenv("GITLAB_TOKEN", "")
    gitlab_project_id: str = os.getenv("GITLAB_PROJECT_ID", "")
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_repo: str = os.getenv("GITHUB_REPO", "")
    git_target_branch: str = os.getenv("GIT_TARGET_BRANCH", "develop")
    worktree_base: str = os.getenv("WORKTREE_BASE", "/tmp/worktrees")

    default_repo_path: str = os.getenv("REPO_PATH", "")

    cursor_api_key: str = os.getenv("CURSOR_API_KEY", "")
    cursor_model: str = os.getenv("CURSOR_MODEL", "composer-2.5")
    cursor_max_edit_attempts: int = int(
        os.getenv("CURSOR_MAX_EDIT_ATTEMPTS", "2")
    )  # Whole-plan Cursor batch passes (1 = apply only; 2 = apply + one repair batch)
    semantic_soft_validation_enabled: bool = (
        os.getenv("SEMANTIC_SOFT_VALIDATION_ENABLED", "true").lower() == "true"
    )
    semantic_warning_density_threshold: float = float(
        os.getenv("SEMANTIC_WARNING_DENSITY_THRESHOLD", "0.5")
    )

    langchain_tracing: bool = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
    langchain_project: str = os.getenv("LANGCHAIN_PROJECT", "sprinklr-jira-solver-dev")

    max_research_iterations: int = int(os.getenv("MAX_RESEARCH_ITERATIONS", "5"))
    max_files_per_research_cycle: int = int(os.getenv("MAX_FILES_PER_RESEARCH_CYCLE", "10"))
    max_lines_per_read: int = int(os.getenv("MAX_LINES_PER_READ", "500"))

    api_max_concurrency: int = int(os.getenv("API_MAX_CONCURRENCY", "2"))


settings = Settings()
