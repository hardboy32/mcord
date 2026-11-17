import asyncio
import logging

try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

import discord
from discord.ext import commands
from mcord_discord import Bot as McordBot
from config import Config
from music_bot import MusicBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("livekit").setLevel(logging.WARNING)
logging.getLogger("livekit_api").setLevel(logging.WARNING)
log = logging.getLogger("mcord-music")

intents = discord.Intents.default()
intents.voice_states = True
# Required for traditional prefix commands such as !play.
# Also enable Message Content Intent in the Discord Developer Portal.
intents.message_content = True


def build_clients():
    clients = []

    mcord_config = Config.from_env("MCORD_BOT_TOKEN")
    if mcord_config:
        mcord_bot = McordBot(command_prefix="!", intents=intents)
        MusicBot(mcord_bot, mcord_config, platform="mcord").register()
        clients.append(("Mcord", mcord_bot, mcord_config.bot_token))

    discord_config = Config.from_env("DISCORD_TOKEN")
    if discord_config:
        discord_bot = commands.Bot(command_prefix="!", intents=intents)
        MusicBot(discord_bot, discord_config, platform="discord").register()
        clients.append(("Discord", discord_bot, discord_config.bot_token))

    if not clients:
        raise RuntimeError(
            "No bot token configured. Set MCORD_BOT_TOKEN, DISCORD_TOKEN, or both."
        )
    return clients


async def event_loop_watchdog():
    loop = asyncio.get_running_loop()
    expected = loop.time() + 5.0
    while True:
        await asyncio.sleep(5)
        now = loop.time()
        lag = now - expected
        expected = now + 5.0
        if lag >= 2.0:
            log.warning("Event loop delayed by %.1fs.", lag)


async def run_client(name, bot, token):
    @bot.event
    async def on_connect():
        log.info("%s gateway connected; waiting for READY.", name)

    @bot.event
    async def on_ready():
        log.info("%s bot ready as %s", name, bot.user)
        try:
            await bot.tree.sync()
            log.info("%s slash commands synced.", name)
        except Exception:
            log.exception("%s slash command sync failed.", name)

    @bot.event
    async def on_disconnect():
        log.warning("%s gateway disconnected.", name)

    @bot.event
    async def on_resumed():
        log.info("%s gateway session resumed.", name)

    log.info("Starting %s bot gateway.", name)
    try:
        await bot.start(token)
        log.warning("%s bot gateway stopped normally.", name)
    except discord.LoginFailure:
        log.exception(
            "%s login failed: token is invalid or belongs to a different bot.",
            name,
        )
    except discord.PrivilegedIntentsRequired:
        log.exception(
            "%s login rejected: enable the required privileged intents "
            "in the Discord Developer Portal.",
            name,
        )
    except Exception:
        log.exception("%s bot gateway crashed.", name)
    finally:
        if not bot.is_closed():
            await bot.close()


async def main():
    clients = build_clients()
    watchdog = asyncio.create_task(event_loop_watchdog())
    tasks = [
        asyncio.create_task(run_client(name, bot, token), name=f"{name}-gateway")
        for name, bot, token in clients
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        watchdog.cancel()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(watchdog, *tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
