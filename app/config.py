from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./support.db"
    # Which business this deployment serves: the folder under verticals/.
    vertical: str = "northwind"
    # Overrides the vertical's own docs folder; empty means use it.
    docs_dir: str = ""
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
    # Email via Postmark (inert until set; see docs/integrations.md)
    postmark_server_token: str = ""
    postmark_from: str = ""  # the support address replies come from
    postmark_inbound_user: str = ""  # Basic-auth credentials in the inbound webhook URL
    postmark_inbound_password: str = ""

    # Connectors enabled for this deployment, comma-separated (e.g. "shopify,ical_availability")
    connectors: str = ""
    # Public calendar links (Airbnb, Booking.com, Vrbo), comma-separated. Busy dates close the
    # property for `check_availability`; unset means the vertical's own blocked dates only.
    ical_urls: str = ""

    # Widget appearance. Brand, greeting, tagline, accent and suggestions come from the
    # vertical's `widget:` block; set one of these only to override it for this deployment.
    widget_brand: str = ""
    widget_greeting: str = ""
    widget_tagline: str = ""
    widget_accent: str = ""
    widget_logo_url: str = ""  # a square image; falls back to the brand's first letter
    widget_position: str = "right"  # right | left
    widget_theme: str = "auto"  # light | dark | auto (follows the visitor's system setting)
    # Sample credentials printed on the demo page so a prospect can see a real order card.
    # Empty in production — the demo page then shows no credentials at all.
    demo_order_number: str = ""
    demo_order_email: str = ""

    # Opening chips, pipe-separated so a question may contain a comma. Empty: the vertical's.
    widget_suggestions: str = ""

    # Eval judge: checks that an answer is faithful to its sources. Empty provider = no judge
    # (deterministic checks only). Same providers as the assistant; the model id is config.
    judge_provider: str = ""  # "" | anthropic | openai_compatible
    judge_model: str = ""
    judge_base_url: str = ""  # openai_compatible only; empty = LLM_BASE_URL
    judge_api_key: str = ""  # empty = ANTHROPIC_API_KEY / LLM_API_KEY for that provider

    max_turns_context: int = 10
    reply_max_tokens: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
