import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

# Public Mcord application. Only the bot token is secret.
APPLICATION_ID = "16"
API_BASE = "https://mcord.ir"


def resolve_ffmpeg_path() -> str:
    """Use the system ffmpeg installed by the hosting environment."""
    configured = os.getenv("FFMPEG_PATH", "").strip()
    if configured:
        return configured
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
            ytdlp_path=os.getenv("YTDLP_PATH", "yt-dlp").strip() or "yt-dlp",
            ffmpeg_path=resolve_ffmpeg_path(),
        )
