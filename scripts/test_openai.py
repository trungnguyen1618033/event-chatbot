"""
Standalone smoke test that POSTs directly to OPENAI_API_BASE_URL.

Supports any OpenAI-compatible endpoint, including an Azure full URL that
already contains /openai/deployments/<name>/chat/completions?api-version=...

Uses only:
    OPENAI_API_BASE_URL   (full URL, can include deployment + api-version)
    OPENAI_API_KEY
    OPENAI_MODEL_NAME

Run:
    .venv/bin/python scripts/test_openai.py
"""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv


def main() -> int:
    load_dotenv()

    base_url = os.environ.get("OPENAI_API_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL_NAME")

    missing = [n for n, v in (("OPENAI_API_BASE_URL", base_url),
                              ("OPENAI_API_KEY", api_key),
                              ("OPENAI_MODEL_NAME", model)) if not v]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    is_azure = "azure.com" in base_url
    auth_header = {"api-key": api_key} if is_azure else {"Authorization": f"Bearer {api_key}"}

    print("Config:")
    print(f"  OPENAI_API_BASE_URL = {base_url}")
    print(f"  OPENAI_API_KEY      = {api_key[:6]}…{api_key[-4:]} ({len(api_key)} chars)")
    print(f"  OPENAI_MODEL_NAME   = {model}")
    print(f"  auth scheme         = {'Azure api-key' if is_azure else 'Bearer token'}")
    print()

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Reply in one short sentence."},
            {"role": "user", "content": "Say hello and tell me your model name."},
        ],
        "temperature": 0.2,
    }

    print("POST →", base_url)
    try:
        r = httpx.post(
            base_url,
            json=body,
            headers={"Content-Type": "application/json", **auth_header},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        print(f"ERROR (network/transport): {exc}", file=sys.stderr)
        return 1

    print(f"HTTP {r.status_code}")
    if r.status_code != 200:
        print("Body:", r.text[:500])
        return 1

    data = r.json()
    msg = data["choices"][0]["message"]["content"]
    print("---")
    print("RESPONSE:", msg)
    print("---")
    print("OK — endpoint is working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
