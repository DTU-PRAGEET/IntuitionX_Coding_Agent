"""LLM abstraction layer — swap provider via LLM_PROVIDER env var."""

import os
from enum import Enum

from langchain_core.language_models import BaseChatModel


class LLMProvider(str, Enum):
    LOCAL = "local"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    GOOGLE = "google"
    SPRINKLR = "sprinklr"


class TaskComplexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_MODEL_MATRIX = {
    LLMProvider.LOCAL: {
        TaskComplexity.LOW: "codellama:7b",
        TaskComplexity.MEDIUM: "codellama:13b",
        TaskComplexity.HIGH: "codellama:34b",
    },
    LLMProvider.ANTHROPIC: {
        TaskComplexity.LOW: "claude-haiku-4-5-20251001",
        TaskComplexity.MEDIUM: "claude-sonnet-4-6",
        TaskComplexity.HIGH: "claude-opus-4-6",
    },
    LLMProvider.OPENAI: {
        TaskComplexity.LOW: "gpt-4o-mini",
        TaskComplexity.MEDIUM: "gpt-4o",
        TaskComplexity.HIGH: "gpt-4o",
    },
    LLMProvider.OPENROUTER: {
        TaskComplexity.LOW: "nvidia/nemotron-3-ultra-550b-a55b:free",
        TaskComplexity.MEDIUM: "nvidia/nemotron-3-ultra-550b-a55b:free",
        TaskComplexity.HIGH: "nvidia/nemotron-3-ultra-550b-a55b:free",
    },
    LLMProvider.GOOGLE: {
        TaskComplexity.LOW: "gemini-2.5-flash",
        TaskComplexity.MEDIUM: "gemini-2.5-flash",
        TaskComplexity.HIGH: "gemini-2.5-flash",
    },
    LLMProvider.SPRINKLR: {
        TaskComplexity.LOW: "gpt-5.2-2025-12-11",
        TaskComplexity.MEDIUM: "gpt-5.2-2025-12-11",
        TaskComplexity.HIGH: "gpt-5.2-2025-12-11",
    },
}


def get_llm(complexity: TaskComplexity = TaskComplexity.MEDIUM) -> BaseChatModel:
    provider = LLMProvider(os.getenv("LLM_PROVIDER", "local"))
    if provider == LLMProvider.OPENROUTER and os.getenv("OPENROUTER_MODEL"):
        model_name = os.getenv("OPENROUTER_MODEL", "")
    elif provider == LLMProvider.GOOGLE and os.getenv("GOOGLE_MODEL"):
        model_name = os.getenv("GOOGLE_MODEL", "")
    elif provider == LLMProvider.SPRINKLR and os.getenv("SPRINKLR_LLM_MODEL"):
        model_name = os.getenv("SPRINKLR_LLM_MODEL", "")
    else:
        model_name = _MODEL_MATRIX[provider][complexity]

    if provider == LLMProvider.LOCAL:
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    if provider == LLMProvider.ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model_name,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )
    if provider == LLMProvider.OPENAI:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    if provider == LLMProvider.OPENROUTER:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        )
    if provider == LLMProvider.GOOGLE:
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = os.getenv("GOOGLE_API_KEY", "")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY is required when LLM_PROVIDER=google")
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
        )
    if provider == LLMProvider.SPRINKLR:
        from src.integrations.llm.sprinklr_router import create_sprinklr_chat_model

        return create_sprinklr_chat_model(model_name)
    raise ValueError(f"Unknown LLM provider: {provider}")
