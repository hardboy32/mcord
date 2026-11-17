# Mcord + Discord Music Bot

One project can run two independent music bots at the same time:

- **Mcord bot** connects through the Mcord SDK and uses `MCORD_BOT_TOKEN`.
- **Discord bot** connects through the official `discord.py` client and uses `DISCORD_TOKEN`.

Each platform has its own gateway connection, slash-command registration, voice connection, guild queues, and playback state. Music search and extraction logic is shared in the same project.

## Environment variables

Set either token or both:

- `MCORD_BOT_TOKEN` — Mcord bot token.
- `DISCORD_TOKEN` — Discord bot token.

Set both variables in the hosting service to run both bots together. Tokens are independent and must never be committed to GitHub.

Optional music settings:

- `YOUTUBE_COOKIES_B64`
- `YOUTUBE_PROXY`
- `YOUTUBE_USER_AGENT`
- `YTDLP_PLAYER_CLIENTS`
- `YTDLP_PATH`
- `FFMPEG_PATH`

## Commands

Both bots register the same slash commands:

- `/play <query>`
- `/queue`
- `/loop <خاموش | همین آهنگ | کل صف>`
- `/pause`
- `/resume`
- `/skip`
- `/stop`

## Voice architecture

- Mcord voice uses `mcord_voice` / LiveKit.
- Discord voice uses the native `discord.py` voice client and FFmpeg audio source.
- The voice adapters are separate so platform-specific APIs do not interfere with each other.
- Each platform maintains independent playback queues and guild sessions.

## Run

The entry point is `run.py`. Install dependencies from `requirements.txt` and add the token variables for the platform or platforms you want to run.

Never commit bot tokens or YouTube cookies to GitHub.
