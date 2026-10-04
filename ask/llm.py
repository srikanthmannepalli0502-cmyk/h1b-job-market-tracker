"""Azure OpenAI chat client, authenticated with Entra ID (managed identity in Azure,
`az login` locally). The OpenAI resource has key auth disabled."""

import os

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

API_VERSION = "2025-04-01-preview"


class AzureChat:
    def __init__(self, endpoint: str | None = None, deployment: str | None = None):
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
        self.client = AzureOpenAI(
            azure_endpoint=endpoint or os.environ["OPENAI_ENDPOINT"],
            azure_ad_token_provider=token_provider,
            api_version=API_VERSION,
            max_retries=2,
            timeout=60,
        )
        self.deployment = deployment or os.environ["OPENAI_DEPLOYMENT"]

    def complete(self, system: str, user: str, *, json_schema: dict | None, effort: str, max_tokens: int) -> str:
        kwargs = {"response_format": json_schema} if json_schema else {}
        response = self.client.chat.completions.create(
            model=self.deployment,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            reasoning_effort=effort,
            max_completion_tokens=max_tokens,
            **kwargs,
        )
        return response.choices[0].message.content or ""
