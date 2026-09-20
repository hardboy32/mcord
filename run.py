import asyncio
import logging

# uvloop improves asyncio scheduling on Linux hosts such as Infrlo.
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

import discord
from mcord_discord import Bot
from config import Config
from music_bot import MusicBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

# LiveKit can be extremely verbose during a reconnect. Avoid flooding the
# Infrlo log stream while still keeping warnings/errors visible.
logging.getLogger("livekit").setLevel(logging.WARNING)
logging.getLogger("livekit_api").setLevel(logging.WARNING)

log = logging.getLogger("mcord-music")

config = Config.from_env()

log.info("Using yt-dlp: %s", config.ytdlp_path)
log.info("Using ffmpeg: %s", config.ffmpeg_path)

intents = discord.Intents.default()
bot = Bot(command_prefix="!", intents=intents)
music = MusicBot(bot, config)


async def event_loop_watchdog():
    """Detect long event-loop stalls without adding blocking work."""
    loop = asyncio.get_running_loop()
    expected = loop.time() + 5.0

    while True:
        await asyncio.sleep(5)
        now = loop.time()
        lag = now - expected
        expected = now + 5.0

        if lag >= 2.0:
            log.warning(
                "Event loop delayed by %.1fs. This is usually host CPU/scheduling or blocking I/O.",
                lag,
            )


music.register()


@bot.event
async def on_ready():
    log.info("Ready as %s", bot.user)
    try:
        await bot.tree.sync()
        log.info("Slash commands synced.")
    except Exception:
        log.exception("Slash command sync failed.")


async def main():
    watchdog = asyncio.create_task(event_loop_watchdog())
    try:
        await bot.start(config.bot_token)
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


asyncio.run(main())
