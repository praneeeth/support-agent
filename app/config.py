from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./support.db"
    docs_dir: str = "data/docs"
    seed: int = 42

    kb_min_score: float = 0.35  # semantic similarity, used when vectors are available
    kb_min_score_keyword: float = 0.15  # BM25 fallback scores sit on a different scale
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    max_failed_lookups: int = 5

    staff_username: str = "staff"
    staff_password: str = "change-me"  # noqa: S105 - demo default, override via env

    # Provider: "anthropic" (paid API) or "openai_compatible"
    # (Ollama locally — free — Groq, Google AI Studio, OpenRouter, vLLM, LM Studio).
    llm_provider: str = "openai_compatible"
    anthropic_api_key: str = ""
    anthropic_model: str = ""
    llm_base_url: str = "http://localhost:11434/v1"  # Ollama's default
    llm_model: str = "qwen3:8b"
    llm_api_key: str = ""
    # WhatsApp (inert until these are set; see docs/integrations.md)
    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""

    # Connectors enabled for this deployment, comma-separated (e.g. "shopify,ical_availability")
    connectors: str = ""

    # Widget appearance. Every client gets these; nothing else about the widget is per-client.
    widget_brand: str = "Northwind Goods"
    widget_greeting: str = "Hi! Ask me about orders, shipping, returns or products."
    widget_tagline: str = "Typically replies instantly"
    widget_accent: str = "#0f766e"
    widget_logo_url: str = ""  # a square image; falls back to the brand's first letter
    widget_position: str = "right"  # right | left
    widget_theme: str = "auto"  # light | dark | auto (follows the visitor's system setting)
    # Sample credentials printed on the demo page so a prospect can see a real order card.
    # Empty in production — the demo page then shows no credentials at all.
    demo_order_number: str = ""
    demo_order_email: str = ""

    # Opening chips, pipe-separated so a question may contain a comma.
    widget_suggestions: str = (
        "Where is my order?|What is your return policy?|Do you ship internationally?"
    )

    max_turns_context: int = 10
    reply_max_tokens: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
