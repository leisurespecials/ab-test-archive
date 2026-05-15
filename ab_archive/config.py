from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ab_archive.sqlite"

JQL_SCOPE = (
    'project = AB AND issuetype = "AB Test" AND status in '
    '(Implemented, "No significant Result", Successful, Unsuccessful)'
)

JIRA_FIELDS = [
    "summary",
    "status",
    "created",
    "updated",
    "labels",
    "description",
    "resolution",
    "customfield_10601",  # Hypothesis
    "customfield_10602",  # Start Date
    "customfield_10603",  # End Date
    "customfield_10700",  # Learnings
    "customfield_11000",  # GA URL
    "customfield_11001",  # Planned Duration
    "customfield_11901",  # Results
    "customfield_12602",  # KPI
    "customfield_12603",  # Test Outcome
]


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    jira_base_url: str
    jira_email: str
    jira_api_token: str
    db_path: Path
    ollama_base_url: str
    ollama_chat_model: str
    ollama_embed_model: str

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        db_path = Path(os.getenv("AB_ARCHIVE_DB") or DEFAULT_DB)
        if not db_path.is_absolute():
            db_path = ROOT / db_path
        return cls(
            jira_base_url=os.getenv("JIRA_BASE_URL", "https://leisurespecials.atlassian.net").rstrip("/"),
            jira_email=os.getenv("JIRA_EMAIL", ""),
            jira_api_token=os.getenv("JIRA_API_TOKEN", ""),
            db_path=db_path,
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            ollama_chat_model=os.getenv("OLLAMA_CHAT_MODEL", "llama3.1:8b"),
            ollama_embed_model=os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
        )

    def require_jira_auth(self) -> None:
        missing = []
        if not self.jira_email:
            missing.append("JIRA_EMAIL")
        if not self.jira_api_token:
            missing.append("JIRA_API_TOKEN")
        if missing:
            joined = ", ".join(missing)
            raise SystemExit(f"Missing {joined}. Add them to .env first.")
