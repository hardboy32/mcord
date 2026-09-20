# Mcord Music Bot

A Discord music bot for Mcord with YouTube search/download, queue controls, loop modes, pause/resume, skip and voice playback.

## Run

The active entry point is `run.py`.

Required environment variable:

- `MCORD_BOT_TOKEN`

YouTube:

- `YOUTUBE_COOKIES_B64` is optional and should only be configured as an Infrlo secret/environment variable.
- Never commit `cookies.txt` or cookie contents to this repository.
- The bot tries multiple YouTube player clients automatically.
- If direct YouTube extraction is blocked, the bot automatically tries several public Piped backends as a second extraction path.
- `YOUTUBE_PROXY` is an optional fallback when both direct extraction and the Piped fallback are unavailable.
- `YOUTUBE_USER_AGENT` can be set when a proxy/cookie session requires a matching browser User-Agent.
- No paid proxy is required by the code; public Piped instances are used only as a best-effort fallback and may change availability.

The bot also installs Deno, ffmpeg and the bgutil PO Token provider automatically when the host does not provide them.

## Commands

- `/play <query>`
- `/queue`
- `/loop <خاموش | همین آهنگ | کل صف>`
- `/pause`
- `/resume`
- `/skip`
- `/stop`

## Security

Do not publish bot tokens, YouTube cookies, proxy credentials or other secrets in source code, commits, issues or logs.
