import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class ConfigError(Exception):
    """Missing or unusable local configuration."""


@dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str
    app_code: str
    broker_id: str
    account_no: str
    deriv_account_no: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        return cls(
            app_id=os.getenv("SETTRADE_APP_ID", "").strip(),
            app_secret=os.getenv("SETTRADE_APP_SECRET", "").strip(),
            app_code=os.getenv("SETTRADE_APP_CODE", "SANDBOX").strip() or "SANDBOX",
            broker_id=os.getenv("SETTRADE_BROKER_ID", "SANDBOX").strip() or "SANDBOX",
            account_no=os.getenv("SETTRADE_ACCOUNT_NO", "").strip(),
            deriv_account_no=os.getenv("SETTRADE_DERIV_ACCOUNT_NO", "").strip(),
        )

    def require_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("SETTRADE_APP_ID", self.app_id),
                ("SETTRADE_APP_SECRET", self.app_secret),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing "
                + ", ".join(missing)
                + ". Copy .env.example to .env and add your Settrade Open API keys."
            )
