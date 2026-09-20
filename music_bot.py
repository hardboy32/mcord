from __future__ import annotations
import asyncio
import base64
import logging
import os
import tempfile
import json
import urllib.parse
import urllib.request
from pathlib import Path
from dataclasses import dataclass
import discord
from discord import app_commands
from mcord_voice import VoiceClient, create_audio_resource

log = logging.getLogger("mcord-music")

# Public Piped instances provide a second YouTube extraction path.
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi-libre.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.privacy.com.de",
    "https://pipedapi.drgns.space",
]

# Public Invidious instances. The last one is intentionally included as a
# rotating fallback because public instances can temporarily rate-limit or
# become unavailable.
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yt.chocolatemoo53.com",
    "https://invidious.tiekoetter.com",
    "https://invidious.f5.si",
]


@dataclass
class Track:
    title: str
    path: Path
    requested_by: str = "unknown"
    query: str = ""


class GuildPlayer:
    def __init__(self, config):
        self.config = config
        self.voice = None
        self.connection = None
        self.queue = []
        self.current = None
        self.temp_files = set()
        self.loop_mode = "off"
        self.loop_items = []

    def _new_voice_client(self):
        self.voice = VoiceClient(
            token=self.config.bot_token,
            api_base=self.config.api_base,
        )

    async def join(self, guild_id, channel_id):
        if self.connection is not None and getattr(self.connection, "channel_id", None) == channel_id:
            return

        if self.connection is not None:
            await self._leave_connection_only()

        # IMPORTANT: mcord_voice/LiveKit already has its own ICE retry loop.
        # Do not wrap voice.join() in a short asyncio.wait_for(), otherwise
        # we cancel that internal recovery while it is still retrying.
        last_error = None
        for attempt in range(1, 3):
            started = asyncio.get_running_loop().time()
            try:
                self._new_voice_client()
                log.info(
                    "Voice join attempt %s/2 guild=%s channel=%s",
                    attempt,
                    guild_id,
                    channel_id,
                )
                self.connection = await self.voice.join(
                    guild_id=guild_id,
                    channel_id=channel_id,
                )
                elapsed = asyncio.get_running_loop().time() - started
                log.info(
                    "Voice connected guild=%s in %.1fs",
                    guild_id,
                    elapsed,
                )
                return
            except Exception as exc:
                last_error = exc
                elapsed = asyncio.get_running_loop().time() - started
                log.warning(
                    "Voice join attempt %s/2 failed after %.1fs: %s",
                    attempt,
                    elapsed,
                    exc,
                )
                self.connection = None
                try:
                    if self.voice is not None:
                        await self.voice.shutdown()
                except Exception:
                    log.exception("voice shutdown after failed join")
                finally:
                    self.voice = None

                if attempt < 2:
                    await asyncio.sleep(1)

        raise RuntimeError(f"Voice connection failed after 2 attempts: {last_error}")

    async def _leave_connection_only(self):
        if self.connection is not None:
            try:
                await self.connection.leave()
            except Exception:
                log.exception("voice leave failed")
            self.connection = None

    async def leave(self):
        await self._leave_connection_only()

        if self.voice is not None:
            try:
                await self.voice.shutdown()
            except Exception:
                log.exception("voice shutdown failed")
            finally:
                self.voice = None

        self.current = None
        self.loop_items.clear()
        for p in list(self.temp_files):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        self.temp_files.clear()

    async def _piped_json(self, url, timeout=10):
        def fetch():
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; McordMusicBot/1.0)",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))

        return await asyncio.to_thread(fetch)

    async def _download_from_invidious(self, query):
        """Resolve/search on Invidious and download through its local proxy."""
        parsed = urllib.parse.urlparse(query)
        video_id = ""

        if parsed.netloc and ("youtube.com" in parsed.netloc or "youtu.be" in parsed.netloc):
            if parsed.netloc.endswith("youtu.be"):
                video_id = parsed.path.strip("/").split("/")[0]
            else:
                video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]

        async def find_video(instance):
            if video_id:
                return video_id, query

            params = urllib.parse.urlencode({
                "q": query,
                "type": "video",
                "sort_by": "relevance",
            })
            data = await self._piped_json(
                f"{instance}/api/v1/search?{params}",
                timeout=8,
            )
            for item in data if isinstance(data, list) else []:
                if item.get("type") == "video" and item.get("videoId"):
                    return item["videoId"], item.get("title") or query
            raise RuntimeError("Invidious returned no video search result.")

        async def try_instance(instance):
            vid, fallback_title = await find_video(instance)
            data = await self._piped_json(
                f"{instance}/api/v1/videos/{urllib.parse.quote(vid, safe='')}",
                timeout=8,
            )

            formats = list(data.get("adaptiveFormats") or [])
            formats += list(data.get("audioStreams") or [])

            audio = [
                f for f in formats
                if "audio/" in (f.get("type") or "")
                or "audio/" in (f.get("mimeType") or "")
            ]
            if not audio:
                raise RuntimeError("Invidious returned no audio stream.")

            def bitrate(fmt):
                try:
                    return int(fmt.get("bitrate") or 0)
                except (TypeError, ValueError):
                    return 0

            preferred = sorted(
                audio,
                key=lambda f: (
                    0 if "audio/mp4" in (
                        f.get("type") or f.get("mimeType") or ""
                    ) else 1,
                    -bitrate(f),
                ),
            )[0]

            itag = preferred.get("itag")
            if not itag:
                raise RuntimeError("Invidious returned an audio stream without itag.")

            # local=true makes Invidious/companion proxy the media stream, so
            # Infrlo does not need to contact a googlevideo host directly.
            stream_url = (
                f"{instance}/latest_version?"
                + urllib.parse.urlencode({
                    "id": vid,
                    "itag": itag,
                    "local": "true",
                })
            )

            out_dir = Path(tempfile.gettempdir()) / "mcord_music"
            out_dir.mkdir(parents=True, exist_ok=True)
            safe_id = "".join(
                ch for ch in vid if ch.isalnum() or ch in "-_"
            )[:80] or "audio"
            mime = preferred.get("type") or preferred.get("mimeType") or ""
            suffix = ".m4a" if "audio/mp4" in mime else ".webm"
            path = out_dir / f"invidious-{safe_id}{suffix}"

            def download_file():
                req = urllib.request.Request(
                    stream_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; McordMusicBot/1.0)",
                        "Accept": "*/*",
                    },
                )
                with urllib.request.urlopen(req, timeout=45) as response, open(path, "wb") as f:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)

            await asyncio.to_thread(download_file)
            if not path.exists() or path.stat().st_size < 1024:
                raise RuntimeError("Invidious returned an empty audio file.")

            self.temp_files.add(path)
            return Track(
                data.get("title") or fallback_title or "YouTube audio",
                path,
                query=query,
            )

        # Try all public instances concurrently. This prevents one dead or
        # rate-limited instance from adding 30-60 seconds of serial delay.
        tasks = {
            asyncio.create_task(try_instance(instance)): instance
            for instance in INVIDIOUS_INSTANCES
        }
        errors = []

        try:
            while tasks:
                done, _ = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    instance = tasks.pop(task)
                    try:
                        result = task.result()
                        for pending in tasks:
                            pending.cancel()
                        if tasks:
                            await asyncio.gather(*tasks, return_exceptions=True)
                        return result
                    except Exception as exc:
                        errors.append(f"{instance}: {exc}")
        finally:
            for pending in tasks:
                pending.cancel()

        raise RuntimeError(
            "All Invidious fallback instances failed: "
            + " | ".join(errors[-5:])
        )

    async def _download_from_piped(self, query):
        """Fallback YouTube resolver that does not contact YouTube directly."""
        parsed = urllib.parse.urlparse(query)
        video_id = ""

        if parsed.netloc and ("youtube.com" in parsed.netloc or "youtu.be" in parsed.netloc):
            if parsed.netloc.endswith("youtu.be"):
                video_id = parsed.path.strip("/").split("/")[0]
            else:
                video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]

        async def get_video_id(instance):
            if video_id:
                return video_id, query
            params = urllib.parse.urlencode({"q": query, "filter": "videos"})
            data = await self._piped_json(f"{instance}/search?{params}", timeout=8)
            items = data.get("items") or []
            for item in items:
                if item.get("type") == "stream" and item.get("url"):
                    return item["url"].split("v=", 1)[-1].split("&", 1)[0], item.get("title") or query
            raise RuntimeError("Piped returned no YouTube search result.")

        errors = []
        for instance in PIPED_INSTANCES:
            try:
                vid, fallback_title = await get_video_id(instance)
                data = await self._piped_json(
                    f"{instance}/streams/{urllib.parse.quote(vid, safe='')}",
                    timeout=10,
                )
                streams = data.get("audioStreams") or []
                if not streams:
                    raise RuntimeError("Piped returned no audio streams.")

                preferred = sorted(
                    streams,
                    key=lambda s: (
                        0 if "audio/mp4" in (s.get("mimeType") or "") else 1,
                        -(s.get("bitrate") or 0),
                    ),
                )[0]
                stream_url = preferred.get("url")
                if not stream_url:
                    raise RuntimeError("Piped returned an invalid audio URL.")

                out_dir = Path(tempfile.gettempdir()) / "mcord_music"
                out_dir.mkdir(parents=True, exist_ok=True)
                safe_id = "".join(ch for ch in vid if ch.isalnum() or ch in "-_")[:80] or "audio"
                suffix = ".m4a" if "audio/mp4" in (preferred.get("mimeType") or "") else ".webm"
                path = out_dir / f"piped-{safe_id}{suffix}"

                def download_file():
                    req = urllib.request.Request(
                        stream_url,
                        headers={"User-Agent": "Mozilla/5.0 (compatible; McordMusicBot/1.0)"},
                    )
                    with urllib.request.urlopen(req, timeout=30) as response, open(path, "wb") as f:
                        while True:
                            chunk = response.read(256 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)

                await asyncio.to_thread(download_file)
                if not path.exists() or path.stat().st_size < 1024:
                    raise RuntimeError("Piped returned an empty audio file.")

                self.temp_files.add(path)
                return Track(
                    data.get("title") or fallback_title or "YouTube audio",
                    path,
                    query=query,
                )

            except Exception as exc:
                errors.append(f"{instance}: {exc}")
                log.warning("Piped fallback failed on %s: %s", instance, exc)

        raise RuntimeError(
            "All Piped fallback instances failed: " + " | ".join(errors[-3:])
        )

    async def download(self, query):
        d = Path(tempfile.gettempdir()) / "mcord_music"
        d.mkdir(parents=True, exist_ok=True)
        out = d / "%(id)s.%(ext)s"
        target = query if query.startswith(("http://", "https://")) else "ytsearch1:" + query

        ffmpeg_dir = str(Path(self.config.ffmpeg_path).resolve().parent)

        cookies_file = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
        cookies_b64 = os.getenv("YOUTUBE_COOKIES_B64", "").strip()
        if not cookies_file and cookies_b64:
            generated_cookie_file = d / "youtube_cookies.txt"
            try:
                generated_cookie_file.write_bytes(
                    base64.b64decode(cookies_b64, validate=True)
                )
                cookies_file = str(generated_cookie_file)
                log.info("YouTube cookies loaded from YOUTUBE_COOKIES_B64.")
            except Exception:
                log.exception("Could not decode YOUTUBE_COOKIES_B64.")

        if not cookies_file:
            for candidate in (
                Path("/app/Config/youtube_cookies.txt"),
                Path("/app/youtube_cookies.txt"),
                Path.cwd() / "Config" / "youtube_cookies.txt",
                Path.cwd() / "youtube_cookies.txt",
            ):
                if candidate.is_file():
                    cookies_file = str(candidate)
                    break

        if cookies_file and Path(cookies_file).is_file():
            log.info("YouTube cookies enabled from %s", cookies_file)
        else:
            cookies_file = ""

        proxy = os.getenv("YOUTUBE_PROXY", "").strip()
        user_agent = os.getenv("YOUTUBE_USER_AGENT", "").strip()

        errors = []

        # Infrlo's datacenter IP is currently challenged by YouTube.
        # Try the proxy resolver first instead of spending 25-35s on clients
        # that are known to be rejected from this hosting IP.
        try:
            log.info("Trying Invidious proxy first.")
            return await self._download_from_invidious(query)
        except Exception as exc:
            errors.append(f"invidious-first: {exc}")
            log.warning("Invidious-first failed: %s", exc)

        client_plans = [
            ("android_vr", False),
            ("tv", False),
            ("web_embedded", True),
            ("web_safari", True),
            (None, True),
            ("mweb", True),
        ]

        for client, use_cookies in client_plans:
            cmd = [
                self.config.ytdlp_path,
                "--no-playlist",
                "--format", "bestaudio[ext=m4a]/bestaudio/best",
                "--ffmpeg-location", ffmpeg_dir,
                "--js-runtimes", f"deno:{self.config.deno_path}",
                "--remote-components", "ejs:npm",
                "--retries", "2",
                "--fragment-retries", "2",
                "--socket-timeout", "15",
                "--no-warnings",
                "--print", "after_move:title",
                "--print", "after_move:filepath",
                "--output", str(out),
            ]

            if use_cookies and cookies_file and Path(cookies_file).is_file():
                cmd += ["--cookies", cookies_file]
            if proxy:
                cmd += ["--proxy", proxy]
            if user_agent:
                cmd += ["--user-agent", user_agent]
            if client:
                cmd += ["--extractor-args", f"youtube:player_client={client}"]
            if self.config.bgutil_path:
                cmd += [
                    "--extractor-args",
                    "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416",
                ]

            cmd.append(target)

            log.info(
                "Trying YouTube client=%s cookies=%s proxy=%s",
                client or "default",
                bool(use_cookies and cookies_file),
                bool(proxy),
            )

            p = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await p.communicate()

            if p.returncode == 0:
                lines = [
                    x.strip()
                    for x in stdout.decode("utf-8", "replace").splitlines()
                    if x.strip()
                ]
                if len(lines) >= 2:
                    path = Path(lines[-1])
                    if path.exists():
                        self.temp_files.add(path)
                        return Track(lines[-2], path, query=query)

            error = stderr.decode("utf-8", "replace").strip()
            diagnostics = [
                line.strip()
                for line in error.splitlines()
                if any(
                    marker in line
                    for marker in (
                        "LOGIN_REQUIRED",
                        "Sign in to confirm",
                        "HTTP Error 403",
                        "HTTP Error 429",
                        "playability status",
                        "Requested format is not available",
                        "Video unavailable",
                        "Private video",
                        "age-restricted",
                        "ERROR:",
                    )
                )
            ]
            if diagnostics:
                error = "\n".join(diagnostics[-10:])

            errors.append(f"{client or 'default'}: {error[-1200:]}")

        try:
            log.warning("Direct YouTube extraction also failed; trying Invidious again.")
            return await self._download_from_invidious(query)
        except Exception as invidious_exc:
            errors.append(f"invidious-fallback: {invidious_exc}")

        try:
            log.warning("Invidious failed; trying Piped fallback.")
            return await self._download_from_piped(query)
        except Exception as piped_exc:
            errors.append(f"piped-fallback: {piped_exc}")

        hint = ""
        if any("LOGIN_REQUIRED" in e or "Sign in to confirm" in e for e in errors):
            hint = (
                " YouTube is rejecting the hosting IP. "
                "The bot tried Invidious proxying and public Piped fallbacks too."
            )

        raise RuntimeError(
            "YouTube extraction failed." + hint + " " + " | ".join(errors[-6:])
        )

    async def play_next(self):
        if self.connection is None:
            log.warning("play_next skipped: no voice connection")
            return

        if not self.queue:
            if self.loop_mode == "queue" and self.loop_items:
                for item in self.loop_items:
                    try:
                        self.queue.append(await self.download(item))
                    except Exception:
                        log.exception("Could not reload loop item: %s", item)

            if not self.queue:
                return

        t = self.queue.pop(0)
        self.current = t

        try:
            log.info("Preparing audio resource: title=%s path=%s", t.title, t.path)
            resource = await asyncio.to_thread(
                create_audio_resource,
                str(t.path),
                ffmpeg_path=self.config.ffmpeg_path,
            )
            log.info("Audio resource ready: title=%s", t.title)
            await self.connection.play(resource)
            log.info("Audio playback started: title=%s", t.title)
            await self.connection.wait_until_idle()
            log.info("Audio playback finished: title=%s", t.title)
        finally:
            try:
                t.path.unlink(missing_ok=True)
            except OSError:
                pass
            self.temp_files.discard(t.path)

            if self.loop_mode == "song" and t.query:
                try:
                    self.queue.insert(0, await self.download(t.query))
                except Exception:
                    log.exception("Could not reload looped song: %s", t.query)

            self.current = None

        if self.queue:
            await self.play_next()

    async def add(self, t):
        self.queue.append(t)
        if self.loop_mode == "queue":
            self.loop_items.append(t.query)

        pos = len(self.queue)

        if self.current is None and self.connection is not None:
            log.info("Starting playback task: title=%s queue_position=%s", t.title, pos)
            task = asyncio.create_task(self.play_next())
            task.add_done_callback(self._playback_task_done)

        return pos

    @staticmethod
    def _playback_task_done(task: asyncio.Task):
        if task.cancelled():
            log.warning("Playback task was cancelled.")
            return
        exc = task.exception()
        if exc:
            log.error("Playback task failed: %s", exc, exc_info=exc)

    async def set_loop(self, mode):
        self.loop_mode = mode
        if mode == "off":
            self.loop_items.clear()
        elif mode == "queue":
            self.loop_items = [t.query for t in self.queue if t.query]
        return self.loop_mode

    async def stop(self):
        self.queue.clear()
        self.loop_items.clear()
        self.loop_mode = "off"

        if self.connection is not None:
            try:
                await self.connection.stop()
            except Exception:
                pass

        self.current = None


class MusicBot:
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.players = {}
        # Keep one play operation per guild at a time. Voice connection
        # negotiation can take several seconds on hosted environments, and
        # overlapping joins can trigger LiveKit reconnects.
        self.play_locks = {}

    def player(self, guild_id):
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(self.config)
        return self.players[guild_id]

    def play_lock(self, guild_id):
        if guild_id not in self.play_locks:
            self.play_locks[guild_id] = asyncio.Lock()
        return self.play_locks[guild_id]

    async def _handle_play(self, interaction, query):
        guild_id = str(interaction.guild.id)
        channel = getattr(
            getattr(interaction.user, "voice", None),
            "channel",
            None,
        )

        if channel is None:
            return await interaction.followup.send(
                "اول وارد Voice Channel شو."
            )

        lock = self.play_lock(guild_id)
        if lock.locked():
            return await interaction.followup.send(
                "یک آهنگ دیگر در حال آماده‌سازی است؛ چند لحظه صبر کن."
            )

        async with lock:
            player = self.player(guild_id)
            try:
                # Extraction happens outside the Discord interaction handler.
                track = await player.download(query)

                # Join only after the file is ready. voice.join() is
                # allowed to finish its own LiveKit/ICE retry cycle.
                await player.join(
                    guild_id,
                    str(channel.id),
                )

                track.requested_by = str(interaction.user)
                pos = await player.add(track)

                log.info("Track queued successfully: title=%s position=%s", track.title, pos)
                try:
                    await interaction.followup.send(
                        f"آهنگ {track.title} به صف اضافه شد. جایگاه: {pos}"
                    )
                except (discord.NotFound, discord.HTTPException) as send_exc:
                    log.warning("Could not send play success message: %s", send_exc)
            except Exception as exc:
                log.exception("play failed")
                try:
                    await interaction.followup.send(
                        f"پخش نشد: {str(exc)[:700]}"
                    )
                except (discord.NotFound, discord.HTTPException) as send_exc:
                    log.warning("Could not send play error message: %s", send_exc)

    @staticmethod
    def _play_task_done(task: asyncio.Task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("background play task failed: %s", exc, exc_info=exc)

    def register(self):
        @self.bot.tree.command(name="play", description="پخش آهنگ از نام یا لینک")
        @app_commands.describe(query="نام آهنگ یا لینک")
        async def play(interaction: discord.Interaction, query: str):
            # Start playback immediately. If Discord's 3-second acknowledgement
            # window has already expired, that must not cancel the music task.
            task = asyncio.create_task(self._handle_play(interaction, query))

            try:
                await interaction.response.defer(thinking=True)
            except discord.NotFound:
                log.warning("play interaction expired before acknowledgement; continuing playback")
            except discord.HTTPException as exc:
                log.warning("play interaction acknowledgement failed: %s; continuing playback", exc)

            task.add_done_callback(self._play_task_done)

        @self.bot.tree.command(name="loop", description="حالت تکرار پخش")
        @app_commands.describe(mode="حالت تکرار")
        @app_commands.choices(mode=[
            app_commands.Choice(name="خاموش", value="off"),
            app_commands.Choice(name="همین آهنگ", value="song"),
            app_commands.Choice(name="کل صف", value="queue"),
        ])
        async def loop(interaction: discord.Interaction, mode: str):
            if interaction.guild is None:
                return await interaction.response.send_message(
                    "این دستور فقط داخل سرور قابل استفاده است."
                )

            player = self.player(str(interaction.guild.id))
            await player.set_loop(mode)

            labels = {
                "off": "خاموش شد.",
                "song": "تکرار همین آهنگ فعال شد.",
                "queue": "تکرار کل صف فعال شد.",
            }
            await interaction.response.send_message(f"🔁 {labels[mode]}")

        @self.bot.tree.command(name="skip", description="آهنگ بعدی")
        async def skip(interaction):
            await interaction.response.defer()
            player = self.player(str(interaction.guild.id))

            if player.connection is None:
                return await interaction.followup.send("چیزی در حال پخش نیست.")

            await player.connection.stop()
            await interaction.followup.send("رفتن به آهنگ بعدی.")

        @self.bot.tree.command(name="stop", description="توقف و خروج از Voice")
        async def stop(interaction):
            await interaction.response.defer()
            player = self.player(str(interaction.guild.id))
            await player.stop()
            await player.leave()
            await interaction.followup.send("پخش متوقف شد و از Voice خارج شدم.")

        @self.bot.tree.command(name="queue", description="نمایش صف")
        async def queue(interaction):
            player = self.player(str(interaction.guild.id))
            lines = (
                [f"در حال پخش: {player.current.title}"]
                if player.current
                else []
            ) + [f"{i}. {t.title}" for i, t in enumerate(player.queue, 1)]

            await interaction.response.send_message(
                "صف خالی است." if not lines else "صف پخش:\n" + "\n".join(lines[:20])
            )

        @self.bot.tree.command(name="pause", description="مکث پخش")
        async def pause(interaction):
            await interaction.response.defer()
            player = self.player(str(interaction.guild.id))
            if player.connection is None:
                return await interaction.followup.send("چیزی در حال پخش نیست.")
            await player.connection.pause()
            await interaction.followup.send("پخش مکث شد.")

        @self.bot.tree.command(name="resume", description="ادامه پخش")
        async def resume(interaction):
            await interaction.response.defer()
            player = self.player(str(interaction.guild.id))
            if player.connection is None:
                return await interaction.followup.send("چیزی برای ادامه نیست.")
            await player.connection.resume()
            await interaction.followup.send("پخش ادامه پیدا کرد.")
