"""
Replay the scenarios from CONVERSATION_LOGS.md through the real LLM
and write a turn-by-turn log to logs/replay_<timestamp>.log.

The DB save step is bypassed: when the chatbot returns scenario=success_save
we just record the draft. To actually persist, run via the FastAPI app
with Postgres up.

Run:
    .venv/bin/python scripts/replay_logs.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from app.services.chatbot import chatbot_service
from app.services.vector_store import vector_store

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


SCENARIO_1: List[str] = [
    "I want to create an event.",
    "Kyoto Jazz Night",
    "March 10th 2026",
    "19:00",
    "VIP at 10000 yen and Regular at 5000 yen",
    "4",
    "January 1st 2026",
    "March 9th 2026",
    "Kyoto Concert Hall",
    "123 Sakyo-ku, Kyoto",
    "1000",
    "Fenix Entertainment",
    "info@fenix.co.jp",
    "Concert",
    "Yes",
]

SCENARIO_2: List[str] = [
    "Create an event called Tokyo Tech Summit on 2026-05-20 at 10am. Organizer is contact@techsummit.invalid",
    "General admission 3000 yen, VIP 8000 yen",
    "10",
    "2026-03-01 to 2026-05-19",
    "Tokyo Big Sight, 3-11-1 Ariake, Koto City, Tokyo, capacity 5000",
    "TechSummit Japan",
    "contact@techsummit.invalid",
    "Sorry, it's contact@techsummit.jp",
    "Conference",
    "Actually, change the date to May 22nd 2026.",
    "Also the purchase end should be May 21st 2026",
    "Yes save it now",
]


async def replay(scenario_id: int, label: str, messages: List[str], log: List[str]) -> None:
    session_id = f"replay-{scenario_id}-{datetime.utcnow():%H%M%S}"
    chatbot_service.reset_session(session_id)

    banner = f"\n{'=' * 78}\nLog {scenario_id} — {label}    (session_id={session_id})\n{'=' * 78}"
    print(banner)
    log.append(banner)

    for i, user_msg in enumerate(messages, 1):
        response = await chatbot_service.handle_message(session_id, user_msg)
        session = chatbot_service._sessions[session_id]

        block = (
            f"\n[turn {i:02d}] USER: {user_msg}\n"
            f"          AI  : scenario={response.scenario}\n"
            f"                last_asked={session.last_asked_field}\n"
            f"                message={response.message}\n"
            f"          DRAFT: {json.dumps(session.draft, default=str, ensure_ascii=False)}"
        )
        print(block)
        log.append(block)

    if session.completed:
        footer = (
            f"\n→ Session marked completed. Final draft:\n"
            f"  {json.dumps(session.draft, default=str, indent=2, ensure_ascii=False)}\n"
            f"  (DB persistence is handled by app/api/routes.py — not invoked in this replay.)"
        )
    else:
        footer = "\n→ Session NOT completed (missing fields or user did not confirm)."
    print(footer)
    log.append(footer)


async def main() -> int:
    vector_store.connect()

    log_lines: List[str] = []
    started = datetime.now()
    header = f"Replay started at {started.isoformat(timespec='seconds')}\nProvider: see settings.llm_provider"
    log_lines.append(header)

    await replay(1, "Successful Event Creation", SCENARIO_1, log_lines)
    await replay(2, "Error Handling & Corrections", SCENARIO_2, log_lines)

    finished = datetime.now()
    log_lines.append(f"\nReplay finished at {finished.isoformat(timespec='seconds')}")
    log_lines.append(f"Elapsed: {(finished - started).total_seconds():.1f}s")

    log_path = LOGS_DIR / f"replay_{started:%Y%m%d_%H%M%S}.log"
    log_path.write_text("\n".join(log_lines), encoding="utf-8")
    print(f"\n\nLog written → {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
