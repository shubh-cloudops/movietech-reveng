"""Shared Gemini call helper."""

import time

from google.genai import errors


def call_with_retry(client, model: str, contents: list, cfg, attempts: int = 4):
    """Retry rate limits (429) and server errors (5xx) with 2s, 4s, 8s backoff."""
    for attempt in range(attempts):
        try:
            return client.models.generate_content(model=model, contents=contents, config=cfg)
        except errors.APIError as e:
            if attempt == attempts - 1 or not (e.code == 429 or e.code >= 500):
                raise
            time.sleep(2 ** (attempt + 1))
