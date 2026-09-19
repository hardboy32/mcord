import logging
import discord
from mcord_discord import Bot
from config import Config
from music_bot import MusicBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

config = Config.from_env()
provider_process = None

if config.bgutil_path:
    import subprocess
    provider_process = subprocess.Popen([config.deno_path, "run", "--allow-env", "--allow-net", "--allow-ffi=.", "--allow-read=.", "src/main.ts"], cwd=config.bgutil_path, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    logging.getLogger("mcord-music").info("BgUtils POT provider starting on 127.0.0.1:4416")
else:
    logging.getLogger("mcord-music").warning("BgUtils POT provider unavailable; continuing without PO tokens.")
log = logging.getLogger("mcord-music")
log.info("Using yt-dlp: %s", config.ytdlp_path)
log.info("Using ffmpeg: %s", config.ffmpeg_path)
log.info("Using Deno: %s", config.deno_path)

intents = discord.Intents.default()
bot = Bot(command_prefix="!", intents=intents)
music = MusicBot(bot, config)
music.register()


@bot.event
async def on_ready():
    log.info("Ready as %s", bot.user)
    try:
        await bot.tree.sync()
        log.info("Slash commands synced.")
    except Exception:
        log.exception("Slash command sync failed.")


bot.run(config.bot_token)
