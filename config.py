import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Config:
    bot_token: str
    api_base: str
    gateway_url: str
    ytdlp_path: str
    ffmpeg_path: str

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("MCORD_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("MCORD_BOT_TOKEN is not set. Copy .env.example to .env and add your bot token.")
        return cls(
            bot_token=token,
            api_base=os.getenv("MCORD_API_BASE", "https://mcord.ir/api").rstrip("/"),
            gateway_url=os.getenv("MCORD_GATEWAY_URL", "").strip(),
            ytdlp_path=os.getenv("YTDLP_PATH", "yt-dlp"),
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
        )
