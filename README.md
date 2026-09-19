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
- `YOUTUBE_PROXY` is an optional fallback when the hosting provider's IP is blocked by YouTube.
- `YOUTUBE_USER_AGENT` can be set when a proxy/cookie session requires a matching browser User-Agent.

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
