import os
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

    # Infrlo may not preserve apt-installed binaries in the runtime PATH.
    # static-ffmpeg supplies both ffmpeg and ffprobe without root access.
    try:
        from static_ffmpeg import run

        ffmpeg, _ffprobe = run.get_or_fetch_platform_executables_else_raise()
        return str(ffmpeg)
    except Exception:
        # Keep the normal system path as a final fallback.
        return "ffmpeg"


@dataclass(frozen=True)
class Config:
    bot_token: str
    application_id: str = APPLICATION_ID
    api_base: str = API_BASE
    ytdlp_path: str = "yt-dlp"
    ffmpeg_path: str = "ffmpeg"

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
        )
