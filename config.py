import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Public Mcord application. Only the bot token is secret.
APPLICATION_ID = "16"
API_BASE = "https://mcord.ir"


def resolve_ffmpeg_path() -> str:
    """Return a working ffmpeg executable, including the portable fallback."""
    configured = os.getenv("FFMPEG_PATH", "").strip()
    if configured:
        return configured

    try:
        from static_ffmpeg import run
        ffmpeg, _ffprobe = run.get_or_fetch_platform_executables_else_raise()
        return str(ffmpeg)
    except Exception:
        return "ffmpeg"


def resolve_deno_path() -> str:
    """Find Deno even when the host does not add ~/.deno/bin to PATH."""
    configured = os.getenv("DENO_PATH", "").strip()
    if configured and Path(configured).exists():
        return configured

    found = shutil.which("deno")
    if found:
        return found

    candidates = [
        Path.home() / ".deno" / "bin" / "deno",
        Path("/usr/local/bin/deno"),
        Path("/usr/bin/deno"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    # Let yt-dlp produce its normal diagnostic if no runtime is present.
    return "deno"


@dataclass(frozen=True)
class Config:
    bot_token: str
    application_id: str = APPLICATION_ID
    api_base: str = API_BASE
    ytdlp_path: str = "yt-dlp"
    ffmpeg_path: str = "ffmpeg"
    deno_path: str = "deno"

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("MCORD_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "MCORD_BOT_TOKEN is not set. Add only the Bot Token as an Infrlo environment variable."
            )

        return cls(
            bot_token=token,
            ytdlp_path=os.getenv("YTDLP_PATH", "yt-dlp"),
            ffmpeg_path=resolve_ffmpeg_path(),
            deno_path=resolve_deno_path(),
        )
