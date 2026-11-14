"""Run the bot in long-polling mode for local development: python -m app.telegram.poll"""
import asyncio
import logging

from ..db import init_db
from .bot import poll_forever

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    asyncio.run(poll_forever())
