# Mcord Music Bot

Discord music bot for Mcord with queue controls, loop modes, pause/resume, skip and voice playback.

## Run

The entry point is `run.py`.

Required environment variable:

- `MCORD_BOT_TOKEN`

## Music extraction

The bot uses **Piped's live public instance list first**. Piped's current API documentation recommends dynamically parsing the public instance list, and its audio stream URLs are served through Piped's proxy infrastructure rather than requiring the bot to contact YouTube directly. citeturn544288view0

The normal `/play` path therefore does not require Deno, PO Tokens, cookies, or a proxy.

There is a small `yt-dlp` direct fallback for hosts where direct YouTube access happens to work.

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
