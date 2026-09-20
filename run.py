import asyncio
import logging
import discord
from mcord_discord import Bot
from config import Config
from music_bot import MusicBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("mcord-music")

# Bot 1 is required. Bot 2 is optional.
config1 = Config.from_env("MCORD_BOT_TOKEN")
config2 = None
try:
    config2 = Config.from_env("MCORD_BOT_TOKEN_2")
except RuntimeError:
    pass

intents1 = discord.Intents.default()
bot1 = Bot(command_prefix="!", intents=intents1)
music1 = MusicBot(bot1, config1)
music1.register()


@bot1.event
async def on_ready():
    log.info("Ready as bot 1: %s", bot1.user)
    try:
        await bot1.tree.sync()
        log.info("Slash commands synced for bot 1.")
    except Exception:
        log.exception("Slash command sync failed for bot 1.")

bots = [bot1]
configs = [config1]

if config2 is not None:
    intents2 = discord.Intents.default()
    bot2 = Bot(command_prefix="!", intents=intents2)
    music2 = MusicBot(bot2, config2)
    music2.register()

    @bot2.event
    async def on_ready():
        log.info("Ready as bot 2: %s", bot2.user)
        try:
            await bot2.tree.sync()
            log.info("Slash commands synced for bot 2.")
        except Exception:
            log.exception("Slash command sync failed for bot 2.")

    bots.append(bot2)
    configs.append(config2)

log.info("Starting %d Discord bot(s).", len(bots))


async def main():
    await asyncio.gather(
        *(bot.start(config.bot_token) for bot, config in zip(bots, configs))
    )


asyncio.run(main())
