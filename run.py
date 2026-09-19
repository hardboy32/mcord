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
    import time
    import urllib.request
    from pathlib import Path

    # The official Deno command runs from server/node_modules and imports
    # ../src/main.ts. Running it from server can break npm/FFI resolution.
    provider_cwd = str(Path(config.bgutil_path) / "node_modules")
    provider_process = subprocess.Popen(
        [
            config.deno_path,
            "run",
            "--allow-env",
            "--allow-net",
            "--allow-ffi=.",
            "--allow-read=.",
            "../src/main.ts",
        ],
        cwd=provider_cwd,
    )
    logging.getLogger("mcord-music").info(
        "BgUtils POT provider starting on 127.0.0.1:4416"
    )

    # Wait briefly so yt-dlp never races the provider during startup.
    provider_ready = False
    for _ in range(30):
        if provider_process.poll() is not None:
            break
        try:
            with urllib.request.urlopen("http://127.0.0.1:4416", timeout=0.5):
                provider_ready = True
                break
        except Exception:
            time.sleep(0.25)

    if provider_process.poll() is not None:
        logging.getLogger("mcord-music").error(
            "BgUtils POT provider exited immediately with code %s",
            provider_process.returncode,
        )
    elif provider_ready:
        logging.getLogger("mcord-music").info(
            "BgUtils POT provider is ready on 127.0.0.1:4416"
        )
    else:
        logging.getLogger("mcord-music").warning(
            "BgUtils POT provider did not become ready; continuing without PO tokens."
        )
else:
    logging.getLogger("mcord-music").warning(
        "BgUtils POT provider unavailable; continuing without PO tokens."
    )
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
