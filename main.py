import asyncio
import logging
from config import Config
from mcord import McordClient
from bot import MusicBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

async def main() -> None:
    config = Config.from_env()
    client = McordClient(config.api_base, config.bot_token)
    bot = MusicBot(client)

    logging.getLogger("mcord-music").info("Mcord Music Bot starting...")
    logging.getLogger("mcord-music").info("Commands prepared: /play /pause /resume /skip /stop /queue")
    logging.getLogger("mcord-music").info("Voice adapter is waiting for the confirmed Mcord Voice API.")

    # Gateway/event handling will be attached here after the exact Mcord Gateway contract is confirmed.
    try:
        await asyncio.Event().wait()
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
