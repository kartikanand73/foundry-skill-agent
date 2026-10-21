"""Model client: OpenAI SDK against the Azure OpenAI v1 endpoint, authenticated with Entra ID.

Locally DefaultAzureCredential uses your `az login` session; when hosted it uses the
agent's managed/workload identity. No API keys.
"""
from __future__ import annotations

from .config import Settings


def make_client(s: Settings):
    if s.fake_model:
        from .fake_llm import FakeClient

        return FakeClient()
    if not s.openai_base_url:
        raise RuntimeError("Set AZURE_OPENAI_ENDPOINT (or FOUNDRY_PROJECT_ENDPOINT) - see .env.example")

    from azure.identity import (AzureCliCredential, AzureDeveloperCliCredential,
                                DefaultAzureCredential, get_bearer_token_provider)
    from openai import OpenAI

    tenant = s.tenant_id or None
    if s.credential == "azd":        # local: reuse `azd auth login`
        cred = AzureDeveloperCliCredential(tenant_id=tenant, process_timeout=30)
    elif s.credential == "cli":      # local: reuse `az login`
        cred = AzureCliCredential(tenant_id=tenant, process_timeout=30)
    else:                            # hosted: managed/workload identity; locally: first available
        cred = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(cred, s.token_scope)
    return OpenAI(
        base_url=s.openai_base_url,
        api_key=token_provider,   # callable: a fresh token is fetched per request
        timeout=s.timeout_s,
        max_retries=2,
    )
