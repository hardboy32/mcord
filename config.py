import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

# Public Mcord application. Bot tokens are the only secrets.
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


@dataclass(frozen=True)
class Config:
    bot_token: str
    application_id: str = APPLICATION_ID
    api_base: str = API_BASE
    ytdlp_path: str = "yt-dlp"
    ffmpeg_path: str = "ffmpeg"

    @classmethod
    def from_env(cls, token_env: str = "MCORD_BOT_TOKEN") -> "Config":
        token = os.getenv(token_env, "").strip()
        if not token:
            raise RuntimeError(
                f"{token_env} is not set. Add the Discord Bot Token as an Infrlo environment variable."
            )

        return cls(
            bot_token=token,
            ytdlp_path=os.getenv("YTDLP_PATH", "yt-dlp").strip() or "yt-dlp",
            ffmpeg_path=resolve_ffmpeg_path(),
        )
