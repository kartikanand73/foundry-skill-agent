"""Settings, read from environment variables (optionally a local .env file). No secrets live here."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

try:  # .env is a local-development convenience only
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _first(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return default


@dataclass(frozen=True)
class Settings:
    openai_base_url: str          # e.g. https://myacct.services.ai.azure.com/openai/v1/
    model: str                    # model *deployment* name
    token_scope: str
    timeout_s: float
    max_tool_rounds: int
    history_turns: int
    skills_dir: Path
    data_dir: Path
    fake_model: bool              # offline scripted model, for tests/demos only
    temperature: float | None     # unset for reasoning models (gpt-5, o-series)
    reasoning_effort: str         # e.g. "low" for gpt-5; empty = model default
    credential: str               # default | azd | cli
    tenant_id: str
    require_roles: bool = False   # hosted: every caller must be a signed-in user with roles

    @staticmethod
    def from_env() -> "Settings":
        endpoint = _first("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            # Hosted agents get FOUNDRY_PROJECT_ENDPOINT injected:
            # https://<account>.services.ai.azure.com/api/projects/<project>
            project = _first("FOUNDRY_PROJECT_ENDPOINT")
            if project:
                p = urlparse(project)
                endpoint = f"{p.scheme}://{p.netloc}"
        base = ""
        if endpoint:
            endpoint = endpoint.rstrip("/")
            base = endpoint + "/" if endpoint.endswith("/openai/v1") else endpoint + "/openai/v1/"
        return Settings(
            openai_base_url=base,
            model=_first("MODEL_DEPLOYMENT_NAME", "AZURE_AI_MODEL_DEPLOYMENT_NAME",
                         "MICROSOFT_FOUNDRY_MODEL_DEPLOYMENT_NAME", default="gpt-4.1-mini"),
            token_scope=_first("AZURE_TOKEN_SCOPE", default="https://ai.azure.com/.default"),
            timeout_s=float(_first("REQUEST_TIMEOUT_SECONDS", default="30")),
            max_tool_rounds=int(_first("MAX_TOOL_ROUNDS", default="3")),
            history_turns=int(_first("HISTORY_TURNS", default="6")),
            skills_dir=Path(_first("SKILLS_DIR", default=str(ROOT / "skills"))),
            data_dir=Path(_first("DATA_DIR", default=str(ROOT / "data"))),
            fake_model=_first("SKILL_AGENT_FAKE_MODEL").lower() in ("1", "true", "yes"),
            temperature=float(t) if (t := _first("MODEL_TEMPERATURE")) else None,
            reasoning_effort=_first("MODEL_REASONING_EFFORT"),
            credential=_first("AZURE_CREDENTIAL", default="default").lower(),
            tenant_id=_first("AZURE_TENANT_ID"),
            require_roles=_first("SKILL_AGENT_REQUIRE_ROLES").lower() in ("1", "true", "yes"),
        )
