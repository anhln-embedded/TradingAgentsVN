"""Configuration and LLM Provider management helper for TradingAgentsVN Web Dashboard.

Provides capabilities to:
- Inspect active LLM Provider, API keys, models, and endpoints
- Safely update .env file and runtime environment
- Provide recommendations for models per provider
"""

import os
from pathlib import Path
from typing import Dict, Any

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.api_key_env import get_api_key_env
from vnapp.settings import runtime_env_path
from dotenv import set_key

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = runtime_env_path()

PROVIDER_METADATA = {
    "openai_compatible": {
        "name": "OpenAI Compatible / 9Router / Proxy",
        "key_env": "OPENAI_COMPATIBLE_API_KEY",
        "default_deep": "Kiro",
        "default_quick": "Kiro",
        "default_url": "http://100.114.80.56:20128/v1",
        "requires_url": True,
    },
    "google": {
        "name": "Google Gemini",
        "key_env": "GOOGLE_API_KEY",
        "default_deep": "gemini-2.5-pro",
        "default_quick": "gemini-2.5-flash",
        "default_url": "",
        "requires_url": False,
    },
    "openai": {
        "name": "OpenAI Official",
        "key_env": "OPENAI_API_KEY",
        "default_deep": "gpt-4o",
        "default_quick": "gpt-4o-mini",
        "default_url": "https://api.openai.com/v1",
        "requires_url": False,
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "key_env": "ANTHROPIC_API_KEY",
        "default_deep": "claude-3-7-sonnet-20250219",
        "default_quick": "claude-3-5-haiku-20241022",
        "default_url": "",
        "requires_url": False,
    },
    "deepseek": {
        "name": "DeepSeek API",
        "key_env": "DEEPSEEK_API_KEY",
        "default_deep": "deepseek-reasoner",
        "default_quick": "deepseek-chat",
        "default_url": "https://api.deepseek.com",
        "requires_url": False,
    },
    "openrouter": {
        "name": "OpenRouter",
        "key_env": "OPENROUTER_API_KEY",
        "default_deep": "anthropic/claude-3.5-sonnet",
        "default_quick": "meta-llama/llama-3.3-70b-instruct",
        "default_url": "https://openrouter.ai/api/v1",
        "requires_url": False,
    },
    "groq": {
        "name": "Groq Cloud (Fast)",
        "key_env": "GROQ_API_KEY",
        "default_deep": "llama-3.3-70b-versatile",
        "default_quick": "llama-3.1-8b-instant",
        "default_url": "https://api.groq.com/openai/v1",
        "requires_url": False,
    },
    "ollama": {
        "name": "Ollama (Local)",
        "key_env": None,
        "default_deep": "llama3.3",
        "default_quick": "llama3.1:8b",
        "default_url": "http://localhost:11434/v1",
        "requires_url": True,
    },
}

def mask_key(key: str | None) -> str:
    """Mask key for safe UI display (e.g. sk-899...58ad)."""
    if not key or len(key) < 8:
        return ""
    return f"{key[:7]}...{key[-4:]}"

def get_current_ai_config() -> Dict[str, Any]:
    """Retrieve active LLM configurations and masked API keys."""
    provider = os.environ.get("TRADINGAGENTS_LLM_PROVIDER") or DEFAULT_CONFIG.get("llm_provider", "openai_compatible")
    deep_model = os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM") or DEFAULT_CONFIG.get("deep_think_llm", "Kiro")
    quick_model = os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM") or DEFAULT_CONFIG.get("quick_think_llm", "Kiro")
    backend_url = os.environ.get("TRADINGAGENTS_LLM_BACKEND_URL") or DEFAULT_CONFIG.get("backend_url", "")

    # Timeout & Retry settings for LLM proxy/rotation
    raw_timeout = os.environ.get("TRADINGAGENTS_LLM_TIMEOUT") or DEFAULT_CONFIG.get("llm_timeout") or 60.0
    try:
        llm_timeout = float(raw_timeout)
    except (ValueError, TypeError):
        llm_timeout = 60.0

    raw_retries = os.environ.get("TRADINGAGENTS_LLM_MAX_RETRIES") or DEFAULT_CONFIG.get("llm_max_retries") or 4
    try:
        llm_max_retries = int(raw_retries)
    except (ValueError, TypeError):
        llm_max_retries = 4

    # Identify key env var
    key_env = get_api_key_env(provider) or (PROVIDER_METADATA.get(provider, {}).get("key_env"))
    raw_key = os.environ.get(key_env, "") if key_env else ""

    return {
        "provider": provider,
        "deep_think_llm": deep_model,
        "quick_think_llm": quick_model,
        "backend_url": backend_url or "",
        "llm_timeout": llm_timeout,
        "llm_max_retries": llm_max_retries,
        "key_env": key_env,
        "api_key_masked": mask_key(raw_key),
        "has_key": bool(raw_key),
        "supported_providers": PROVIDER_METADATA,
    }

def update_ai_config(
    provider: str,
    api_key: str | None = None,
    deep_think_llm: str | None = None,
    quick_think_llm: str | None = None,
    backend_url: str | None = None,
    llm_timeout: float | int | None = None,
    llm_max_retries: int | None = None,
) -> Dict[str, Any]:
    """Update environment variables and persist to .env."""
    provider = provider.strip().lower()
    meta = PROVIDER_METADATA.get(provider, {})

    updates = {}
    updates["TRADINGAGENTS_LLM_PROVIDER"] = provider

    if deep_think_llm:
        updates["TRADINGAGENTS_DEEP_THINK_LLM"] = deep_think_llm.strip()
    if quick_think_llm:
        updates["TRADINGAGENTS_QUICK_THINK_LLM"] = quick_think_llm.strip()
    if backend_url is not None:
        updates["TRADINGAGENTS_LLM_BACKEND_URL"] = backend_url.strip()
    if llm_timeout is not None:
        updates["TRADINGAGENTS_LLM_TIMEOUT"] = str(float(llm_timeout))
    if llm_max_retries is not None:
        updates["TRADINGAGENTS_LLM_MAX_RETRIES"] = str(int(llm_max_retries))

    key_env = get_api_key_env(provider) or meta.get("key_env")
    if key_env and api_key and api_key.strip():
        updates[key_env] = api_key.strip()
        if provider == "openai_compatible":
            # Also keep OPENAI_API_KEY in sync for compatibility
            updates["OPENAI_API_KEY"] = api_key.strip()

    # Persist first, so a disk error cannot leave runtime and disk disagreeing.
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    for key, value in updates.items():
        set_key(str(ENV_PATH), key, value, quote_mode="always")

    # 1. Update os.environ
    for k, v in updates.items():
        os.environ[k] = v

    # 2. Update DEFAULT_CONFIG in memory
    DEFAULT_CONFIG["llm_provider"] = provider
    if deep_think_llm:
        DEFAULT_CONFIG["deep_think_llm"] = deep_think_llm.strip()
    if quick_think_llm:
        DEFAULT_CONFIG["quick_think_llm"] = quick_think_llm.strip()
    if backend_url is not None:
        DEFAULT_CONFIG["backend_url"] = backend_url.strip()
    if llm_timeout is not None:
        DEFAULT_CONFIG["llm_timeout"] = float(llm_timeout)
    if llm_max_retries is not None:
        DEFAULT_CONFIG["llm_max_retries"] = int(llm_max_retries)

    return get_current_ai_config()
