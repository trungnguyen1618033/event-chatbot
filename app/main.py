from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.services.database import db_service
from app.services.vector_store import vector_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up …")
    await db_service.connect()
    await db_service.create_tables()
    vector_store.connect()
    yield
    logger.info("Shutting down …")
    await db_service.disconnect()


app = FastAPI(
    title="Event Creation Chatbot API",
    version="1.0.0",
    description="AI-assisted event creation via conversational interface.",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# API routes
app.include_router(router, prefix="/api")


# Root — serve the chat widget
@app.get("/", include_in_schema=False)
async def serve_chat_widget():
    widget = static_dir / "chat_widget.html"
    if widget.exists():
        return FileResponse(str(widget))
    return {"message": "Event Chatbot API is running. Visit /docs for the API."}


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}
