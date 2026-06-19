"""Environment-based configuration loader."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env.local first, then .env
_env_local = Path(__file__).resolve().parents[2] / ".env.local"
_env_file = Path(__file__).resolve().parents[2] / ".env"
if _env_local.exists():
    load_dotenv(_env_local)
elif _env_file.exists():
    load_dotenv(_env_file)


@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "local")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    token_budget: int = int(os.getenv("TOKEN_BUDGET", "150000"))

    jira_server_url: str = os.getenv("JIRA_SERVER_URL", "")
    jira_email: str = os.getenv("JIRA_EMAIL", "")
    jira_api_token: str = os.getenv("JIRA_API_TOKEN", "")

    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    mongo_url: str = os.getenv("MONGO_URL", "mongodb://localhost:27017")

    gitlab_url: str = os.getenv("GITLAB_URL", "")
    gitlab_token: str = os.getenv("GITLAB_TOKEN", "")
    gitlab_project_id: str = os.getenv("GITLAB_PROJECT_ID", "")
    git_target_branch: str = os.getenv("GIT_TARGET_BRANCH", "develop")
    worktree_base: str = os.getenv("WORKTREE_BASE", "/tmp/worktrees")

    default_repo_path: str = os.getenv("REPO_PATH", "")

    langchain_tracing: bool = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
    langchain_project: str = os.getenv("LANGCHAIN_PROJECT", "sprinklr-jira-solver-dev")

    max_research_iterations: int = int(os.getenv("MAX_RESEARCH_ITERATIONS", "5"))
    max_files_per_research_cycle: int = int(os.getenv("MAX_FILES_PER_RESEARCH_CYCLE", "10"))
    max_lines_per_read: int = int(os.getenv("MAX_LINES_PER_READ", "500"))


settings = Settings()
