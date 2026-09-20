# Mcord Music Bot

Discord music bot for Mcord with queue controls, loop modes, pause/resume, skip and voice playback.

## Run

The entry point is `run.py`.

Required environment variable:

- `MCORD_BOT_TOKEN`

## Music extraction

The bot tries music sources in this order:

1. **SoundCloud search/direct URL** — normal song-name searches use SoundCloud first, so they do not depend on YouTube.
2. **Piped** — used as a YouTube-compatible fallback when a public Piped instance is reachable.
3. **yt-dlp direct YouTube** — last fallback for hosts where YouTube accepts the server IP.

The normal SoundCloud path does not require Deno, PO Tokens, YouTube cookies, or a proxy.

Optional variables:

- `YOUTUBE_COOKIES_B64`
- `YOUTUBE_PROXY`
- `YOUTUBE_USER_AGENT`

Never commit secrets to GitHub.

## Commands

- `/play <query>`
- `/queue`
- `/loop <خاموش | همین آهنگ | کل صف>`
- `/pause`
- `/resume`
- `/skip`
- `/stop`
