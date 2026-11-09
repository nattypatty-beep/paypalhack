"""Configuration for MandatePay.

Everything is read from environment variables (a local ``.env`` file is loaded
automatically).  The defaults are deliberately safe:

* no PayPal credentials  -> deterministic mock PayPal, so the demo always runs
* no LLM credentials     -> deterministic planner/verifier, clearly labelled
* sandbox only           -> live PayPal requires the extra ALLOW_LIVE=1 switch
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so the app has no extra runtime dependency."""
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- app ---------------------------------------------------------------
    app_name: str = "MandatePay"
    data_dir: Path = ROOT / "data"
    samples_dir: Path = ROOT / "samples"
    demo_mode: bool = True
    max_upload_mb: int = 8
    approval_ttl_minutes: int = 30
    approval_token_ttl_minutes: int = 20

    # --- access control ----------------------------------------------------
    # Optional HTTP Basic auth for public demos. Both must be set to enable it.
    basic_auth_user: str = ""
    basic_auth_password: str = ""

    # --- PayPal ------------------------------------------------------------
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_env: str = "sandbox"
    paypal_webhook_id: str = ""
    paypal_payouts_enabled: bool = True
    paypal_force_mock: bool = False
    allow_live: bool = False
    freelancer_name: str = "Avery Quinn"
    freelancer_email: str = "freelancer@example.com"
    platform_fee_bps: int = 0

    # --- LLM ---------------------------------------------------------------
    llm_provider: str = "auto"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 40
    llm_min_confidence: float = 0.7

    # --- derived -----------------------------------------------------------
    @property
    def auth_enabled(self) -> bool:
        return bool(self.basic_auth_user and self.basic_auth_password)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "mandatepay.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def paypal_real(self) -> bool:
        if self.paypal_force_mock:
            return False
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def paypal_environment(self) -> str:
        if not self.paypal_real:
            return "mock"
        env = self.paypal_env.lower()
        if env == "live" and not self.allow_live:
            return "sandbox"
        return "live" if env == "live" else "sandbox"

    @property
    def paypal_base_url(self) -> str:
        if self.paypal_environment == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @property
    def llm_provider_resolved(self) -> str:
        provider = (self.llm_provider or "auto").lower()
        if provider != "auto":
            return provider
        if self.llm_api_key and self.llm_model:
            return "openai"
        if self.llm_model and "11434" in self.llm_base_url:
            return "ollama"
        return "none"

    @property
    def llm_enabled(self) -> bool:
        provider = self.llm_provider_resolved
        if provider in {"none", ""}:
            return False
        if provider == "ollama":
            return bool(self.llm_model)
        return bool(self.llm_model and self.llm_api_key)

    @property
    def llm_base_url_resolved(self) -> str:
        if self.llm_base_url:
            return self.llm_base_url.rstrip("/")
        provider = self.llm_provider_resolved
        if provider == "anthropic":
            return "https://api.anthropic.com"
        if provider == "ollama":
            return "http://localhost:11434/v1"
        return "https://api.openai.com/v1"


def load_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    settings = Settings(
        app_name=_str("APP_NAME", "MandatePay"),
        data_dir=Path(_str("DATA_DIR", str(ROOT / "data"))),
        samples_dir=Path(_str("SAMPLES_DIR", str(ROOT / "samples"))),
        demo_mode=_bool("DEMO_MODE", True),
        max_upload_mb=_int("MAX_UPLOAD_MB", 8),
        approval_ttl_minutes=_int("APPROVAL_TTL_MINUTES", 30),
        approval_token_ttl_minutes=_int("APPROVAL_TOKEN_TTL_MINUTES", 20),
        basic_auth_user=_str("BASIC_AUTH_USER"),
        basic_auth_password=_str("BASIC_AUTH_PASSWORD"),
        paypal_client_id=_str("PAYPAL_CLIENT_ID"),
        paypal_client_secret=_str("PAYPAL_CLIENT_SECRET"),
        paypal_env=_str("PAYPAL_ENV", "sandbox"),
        paypal_webhook_id=_str("PAYPAL_WEBHOOK_ID"),
        paypal_payouts_enabled=_bool("PAYPAL_PAYOUTS_ENABLED", True),
        paypal_force_mock=_bool("PAYPAL_FORCE_MOCK", False),
        allow_live=_bool("ALLOW_LIVE", False),
        freelancer_name=_str("FREELANCER_NAME", "Avery Quinn"),
        freelancer_email=_str("FREELANCER_EMAIL", "freelancer@example.com"),
        platform_fee_bps=_int("PLATFORM_FEE_BPS", 0),
        llm_provider=_str("LLM_PROVIDER", "auto"),
        llm_base_url=_str("LLM_BASE_URL"),
        llm_api_key=_str("LLM_API_KEY"),
        llm_model=_str("LLM_MODEL"),
        llm_timeout_seconds=_int("LLM_TIMEOUT_SECONDS", 40),
        llm_min_confidence=float(_str("LLM_MIN_CONFIDENCE", "0.7") or 0.7),
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    return settings
