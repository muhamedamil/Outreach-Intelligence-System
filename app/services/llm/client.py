# app/services/llm/client.py

import asyncio
from typing import Optional

from groq import AsyncGroq

from app.config.settings import settings


# CLIENT SINGLETON
class GroqClient:
    _client: Optional[AsyncGroq] = None

    @classmethod
    def get_client(cls) -> AsyncGroq:
        if cls._client is None:
            cls._client = AsyncGroq(
                api_key=settings.LLM_API_KEY
            )
        return cls._client


# -------------------------
# CORE GENERATION FUNCTION
# -------------------------
async def llm_generate(
    prompt: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None
) -> Optional[str]:
    """
    Async LLM call with:
    - retry
    - timeout
    - failure safety
    """

    client = GroqClient.get_client()

    temperature = temperature if temperature is not None else settings.LLM_TEMPERATURE
    max_tokens = max_tokens if max_tokens is not None else settings.LLM_MAX_TOKENS

    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.LLM_MODEL,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                timeout=settings.LLM_TIMEOUT
            )

            # SAFE EXTRACTION
            content = response.choices[0].message.content

            if not content:
                return None

            return content.strip()

        except asyncio.TimeoutError:
            error = "LLM timeout"

        except Exception as e:
            error = str(e)

        # RETRY BACKOFF
        if attempt < settings.LLM_MAX_RETRIES:
            await asyncio.sleep(1.5 ** attempt)
        else:
            return None

    return None