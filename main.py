"""FastAPI app for Hugging Face Spaces keep-alive + bot lifecycle."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.api.studio_routes import router as studio_router
from src.bot.handlers import run_bot
from src.config import get_settings
from src.db.supabase_client import get_supabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_bot_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _bot_task
    settings = get_settings()

    if settings.telegram_bot_token:
        bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        _bot_task = asyncio.create_task(run_bot(bot))
        logger.info("Telegram bot polling started")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN missing — API only mode")

    yield

    if _bot_task:
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="The Persona Factory", lifespan=lifespan)
app.include_router(studio_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "The Persona Factory", "status": "ok"}


def _database_healthcheck() -> None:
    get_supabase().table("style_modes").select("id").limit(1).execute()


@app.get("/health", response_model=None)
async def health():
    try:
        await asyncio.to_thread(_database_healthcheck)
    except Exception as exc:  # noqa: BLE001
        logger.error("Database health check failed: %s", exc)
        return JSONResponse(
            {"status": "degraded", "database": "unavailable"},
            status_code=503,
        )
    return {"status": "ok", "database": "connected"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "7860"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
