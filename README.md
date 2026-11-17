# Mcord + Discord Music Bot

One project can run two independent music bots at the same time:

- **Mcord bot** connects through the Mcord SDK and uses `MCORD_BOT_TOKEN`.
- **Discord bot** connects through the official `discord.py` client and uses `DISCORD_TOKEN`.

Each platform has its own gateway connection, slash-command registration, voice connection, guild queues, and playback state. Music search and extraction logic is shared in the same project.

## Deploy on Railway

This repository includes a root-level `Dockerfile`. Railway will detect it and build the service as a Docker deployment. The image installs FFmpeg and the Python dependencies, then starts `python run.py`.

1. Create a new Railway project and choose **Deploy from GitHub repo**.
2. Select `hardboy32/mcord`.
3. In the service's **Variables** tab, add the bot token variables below.
4. Deploy the service and inspect its deployment logs. You should see a ready message for each configured bot.

This is a worker service; it does not need a public domain or an exposed HTTP port. Keep one running replica to avoid starting duplicate bot sessions.

## Environment variables

Set either token or both:

- `MCORD_BOT_TOKEN` — Mcord bot token.
- `DISCORD_TOKEN` — Discord bot token.

Set both variables in Railway to run both bots together. Tokens are independent and must never be committed to GitHub.

Optional music settings:

- `YOUTUBE_COOKIES_B64` — YouTube cookies encoded as Base64. Paste the encoded value into Railway Variables; do not upload the cookies file to GitHub.
- `YOUTUBE_COOKIES_FILE` — path to an existing cookies file (usually not needed on Railway).
- `YOUTUBE_PROXY` — optional proxy URL; multiple proxies may be comma-separated.
- `YOUTUBE_USER_AGENT`
- `YTDLP_PLAYER_CLIENTS`
- `YTDLP_PATH` — defaults to `yt-dlp`.
- `FFMPEG_PATH` — defaults to the system `ffmpeg` installed in the Docker image.

Railway's container filesystem is temporary unless a volume is attached. The bot's downloaded audio and decoded cookie file are temporary runtime files and are recreated as needed; no persistent volume is required for them.

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

## Run locally

Install dependencies from `requirements.txt`, install FFmpeg separately, and add the token variables to a local `.env` file. Then run:

```bash
python run.py
```

Never commit bot tokens or YouTube cookies to GitHub.
