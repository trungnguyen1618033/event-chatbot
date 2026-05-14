"""
Standalone smoke test for Azure OpenAI credentials in .env.

Run:
    .venv/bin/python scripts/test_azure_openai.py

Reads OPENAI_API_BASE_URL / OPENAI_API_KEY / OPENAI_API_LLM_DEPLOY_NAME /
OPENAI_API_VERSION from .env, sends one chat turn, prints the response.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI


def main() -> int:
    load_dotenv()

    required = {
        "OPENAI_API_BASE_URL": os.environ.get("OPENAI_API_BASE_URL"),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
        "OPENAI_API_LLM_DEPLOY_NAME": os.environ.get("OPENAI_API_LLM_DEPLOY_NAME"),
        "OPENAI_API_VERSION": os.environ.get("OPENAI_API_VERSION"),
    }

    missing = [k for k, v in required.items() if not v]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    print("Config:")
    for k, v in required.items():
        shown = v if k != "OPENAI_API_KEY" else f"{v[:6]}…{v[-4:]} ({len(v)} chars)"
        print(f"  {k:30s} = {shown}")
    print()

    llm = AzureChatOpenAI(
        azure_endpoint=required["OPENAI_API_BASE_URL"],
        api_key=required["OPENAI_API_KEY"],
        azure_deployment=required["OPENAI_API_LLM_DEPLOY_NAME"],
        api_version=required["OPENAI_API_VERSION"],
        temperature=0.2,
    )

    print("Sending test prompt…")
    response = llm.invoke(
        [
            SystemMessage(content="You are a helpful assistant. Reply in one short sentence."),
            HumanMessage(content="Say hello and tell me your model name."),
        ]
    )

    print("---")
    print("RESPONSE:")
    print(response.content)
    print("---")
    print("OK — Azure OpenAI credentials are working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
