from __future__ import annotations
import asyncio
import base64
import logging
import os
import shutil
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

# Piped maintains a live JSON list of public API instances. The old
# hard-coded list became stale very quickly and caused the bot to keep trying
# dead backends. We refresh this list at runtime.
PIPED_INSTANCES_URL = "https://piped-instances.kavin.rocks/"
PIPED_FALLBACK_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.tokhmi.xyz",
    "https://pipedapi.moomoo.me",
    "https://pipedapi.syncpundit.io",
    "https://api-piped.mha.fi",
    "https://pipedapi.leptons.xyz",
    "https://pipedapi.astartes.nl",
    "https://api.piped.yt",
]
SOUNDCLOUD_AUDIO_FORMATS = "http_aac,hls_aac,http_opus,hls_opus,http_mp3,hls_mp3"


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
        # Piped's instance list changes frequently. Cache it for a short time
        # so /play does not depend on a stale hard-coded list.
        self._piped_instances = []
        self._piped_instances_loaded_at = 0.0
        # Signals an explicit /skip or /stop so duration-based completion
        # detection does not hold the queue for the whole song.
        self.playback_interrupt = None

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
        if self.playback_interrupt is not None:
            self.playback_interrupt.set()
            self.playback_interrupt = None

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

    def _urlopen(self, request, timeout, proxy=""):
        # urllib does not automatically use YOUTUBE_PROXY just because yt-dlp
        # receives --proxy. Keep the HTTP fallbacks on the same egress IP.
        if proxy:
            handler = urllib.request.ProxyHandler({
                "http": proxy,
                "https": proxy,
            })
            opener = urllib.request.build_opener(handler)
            return opener.open(request, timeout=timeout)
        return urllib.request.urlopen(request, timeout=timeout)

    async def _get_piped_instances(self):
        """Return a fresh, usable set of public Piped API instances."""
        now = asyncio.get_running_loop().time()
        if self._piped_instances and now - self._piped_instances_loaded_at < 600:
            return list(self._piped_instances)

        def fetch_instances():
            req = urllib.request.Request(
                PIPED_INSTANCES_URL,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; McordMusicBot/1.0)",
                    "Accept": "application/json",
                },
            )
            with self._urlopen(req, timeout=8, proxy=os.getenv("YOUTUBE_PROXY", "").strip()) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))

            result = []
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    api_url = str(item.get("api_url") or "").strip().rstrip("/")
                    if not api_url.startswith("https://"):
                        continue
                    result.append((bool(item.get("cdn")), api_url))

            # CDN-backed instances are generally the intended public setup.
            result.sort(key=lambda item: (not item[0], item[1]))
            return [url for _cdn, url in result]

        try:
            instances = await asyncio.to_thread(fetch_instances)
        except Exception as exc:
            log.warning("Could not refresh Piped instance list: %s", exc)
            instances = []

        merged = []
        for url in instances + PIPED_FALLBACK_INSTANCES:
            if url not in merged:
                merged.append(url)

        self._piped_instances = merged[:12]
        self._piped_instances_loaded_at = now
        log.info("Loaded %d Piped API instances.", len(self._piped_instances))
        return list(self._piped_instances)

    async def _piped_json(self, url, timeout=10):
        proxy = os.getenv("YOUTUBE_PROXY", "").strip()

        def fetch():
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; McordMusicBot/1.0)",
                    "Accept": "application/json",
                },
            )
            with self._urlopen(req, timeout=timeout, proxy=proxy) as response:
                return json.loads(response.read().decode("utf-8", "replace"))

        return await asyncio.to_thread(fetch)

    async def _download_from_soundcloud(self, query):
        """Search/download a SoundCloud track without touching YouTube."""
        parsed = urllib.parse.urlparse(query)
        is_url = bool(parsed.netloc and "soundcloud.com" in parsed.netloc.lower())

        # Do not send YouTube/other URLs to the SoundCloud search extractor.
        if query.startswith(("http://", "https://")) and not is_url:
            raise RuntimeError("not a SoundCloud URL")

        target = query if is_url else f"scsearch1:{query}"

        out_dir = Path(tempfile.gettempdir()) / "mcord_music"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "sc-%(id)s.%(ext)s"
        ffmpeg_dir = str(Path(self.config.ffmpeg_path).resolve().parent)
        proxy = os.getenv("YOUTUBE_PROXY", "").strip()
        user_agent = os.getenv("YOUTUBE_USER_AGENT", "").strip()

        cmd = [
            self.config.ytdlp_path,
            "--no-playlist",
            "--format", "bestaudio/best",
            "--ffmpeg-location", ffmpeg_dir,
            "--extractor-args", f"soundcloud:formats={SOUNDCLOUD_AUDIO_FORMATS}",
            "--retries", "1",
            "--fragment-retries", "1",
            "--socket-timeout", "12",
            "--no-warnings",
            "--print", "after_move:title",
            "--print", "after_move:filepath",
            "--output", str(out),
        ]
        if proxy:
            cmd += ["--proxy", proxy]
        if user_agent:
            cmd += ["--user-agent", user_agent]
        cmd.append(target)

        log.info("Trying SoundCloud %s", "URL" if is_url else "search")
        p = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await p.communicate()

        if p.returncode != 0:
            error = stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(
                "SoundCloud extraction failed: "
                + (error[-1600:] or "unknown error")
            )

        lines = [
            x.strip()
            for x in stdout.decode("utf-8", "replace").splitlines()
            if x.strip()
        ]
        if len(lines) < 2:
            raise RuntimeError("SoundCloud returned no playable track.")

        path = Path(lines[-1])
        title = lines[-2]
        if not path.exists():
            raise RuntimeError(f"SoundCloud output file was not created: {path}")

        duration = await self._media_duration(path)
        if duration is None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError("SoundCloud returned invalid media.")

        self.temp_files.add(path)
        log.info(
            "SoundCloud extraction succeeded: title=%s duration=%.2fs",
            title,
            duration,
        )
        return Track(title, path, query=query)

    async def _download_from_piped(self, query):
        """Resolve and download audio through Piped's own media proxy."""
        parsed = urllib.parse.urlparse(query)
        video_id = ""

        if parsed.netloc and ("youtube.com" in parsed.netloc or "youtu.be" in parsed.netloc):
            if parsed.netloc.endswith("youtu.be"):
                video_id = parsed.path.strip("/").split("/")[0]
            else:
                video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]

        instances = await self._get_piped_instances()
        if not instances:
            raise RuntimeError("No Piped instances are available.")

        async def try_instance(instance):
            fallback_title = query

            if video_id:
                vid = video_id
            else:
                params = urllib.parse.urlencode({
                    "q": query,
                    "filter": "music_songs",
                })
                data = await self._piped_json(
                    f"{instance}/search?{params}",
                    timeout=8,
                )
                items = data.get("items") or []
                vid = ""
                for item in items:
                    if item.get("type") == "stream" and item.get("url"):
                        raw_url = item["url"]
                        if "v=" in raw_url:
                            vid = raw_url.split("v=", 1)[-1].split("&", 1)[0]
                        if not vid:
                            vid = str(item.get("url") or "").strip().rstrip("/").split("/")[-1]
                        fallback_title = item.get("title") or query
                        if vid:
                            break
                if not vid:
                    # Some instances ignore music_songs and return normal video
                    # results, so retry the same search without the music filter.
                    params = urllib.parse.urlencode({"q": query, "filter": "videos"})
                    data = await self._piped_json(
                        f"{instance}/search?{params}",
                        timeout=8,
                    )
                    for item in data.get("items") or []:
                        if item.get("type") == "stream":
                            raw_url = str(item.get("url") or "")
                            if "v=" in raw_url:
                                vid = raw_url.split("v=", 1)[-1].split("&", 1)[0]
                            else:
                                vid = raw_url.rstrip("/").split("/")[-1]
                            fallback_title = item.get("title") or query
                            if vid:
                                break

                if not vid:
                    raise RuntimeError("Piped returned no search result.")

            data = await self._piped_json(
                f"{instance}/streams/{urllib.parse.quote(vid, safe='')}",
                timeout=10,
            )
            streams = [
                s for s in (data.get("audioStreams") or [])
                if not s.get("videoOnly") and s.get("url")
            ]
            if not streams:
                raise RuntimeError("Piped returned no audio stream.")

            def bitrate(stream):
                try:
                    return int(stream.get("bitrate") or 0)
                except (TypeError, ValueError):
                    return 0

            preferred = sorted(
                streams,
                key=lambda s: (
                    0 if "audio/mp4" in (s.get("mimeType") or "") else 1,
                    -bitrate(s),
                ),
            )[0]
            stream_url = preferred["url"]

            # Current Piped returns a pipedproxy URL here. Downloading that
            # URL keeps the media transfer away from YouTube/googlevideo and
            # is the important part that lets this work from datacenter hosts.
            out_dir = Path(tempfile.gettempdir()) / "mcord_music"
            out_dir.mkdir(parents=True, exist_ok=True)
            safe_id = "".join(ch for ch in vid if ch.isalnum() or ch in "-_")[:80] or "audio"
            mime = preferred.get("mimeType") or ""
            suffix = ".m4a" if "audio/mp4" in mime else ".webm"
            path = out_dir / f"piped-{safe_id}{suffix}"

            def download_file():
                req = urllib.request.Request(
                    stream_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; McordMusicBot/1.0)",
                        "Accept": "*/*",
                    },
                )
                with self._urlopen(
                    req,
                    timeout=45,
                    proxy=os.getenv("YOUTUBE_PROXY", "").strip(),
                ) as response, open(path, "wb") as f:
                    log.info(
                        "Piped media response: instance=%s status=%s content_type=%s content_length=%s",
                        instance,
                        getattr(response, "status", "?"),
                        response.headers.get("Content-Type"),
                        response.headers.get("Content-Length"),
                    )
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)

            await asyncio.to_thread(download_file)

            if not path.exists() or path.stat().st_size < 1024:
                raise RuntimeError("Piped returned an empty audio file.")

            duration = await self._media_duration(path)
            if duration is None:
                size = path.stat().st_size if path.exists() else 0
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise RuntimeError(
                    f"Piped returned an invalid/incomplete audio file (size={size})."
                )

            self.temp_files.add(path)
            return Track(
                data.get("title") or fallback_title or "YouTube audio",
                path,
                query=query,
            )

        # Try several live instances in parallel. This avoids getting stuck
        # behind one dead public backend and is much faster than the old
        # serial fallback chain.
        tasks = {
            asyncio.create_task(try_instance(instance)): instance
            for instance in instances[:8]
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
                        log.info("Piped extraction succeeded via %s", instance)
                        return result
                    except Exception as exc:
                        errors.append(f"{instance}: {exc}")
                        log.warning("Piped failed on %s: %s", instance, exc)
        finally:
            for pending in tasks:
                pending.cancel()

        raise RuntimeError(
            "All live Piped instances failed: " + " | ".join(errors[-8:])
        )

    async def download(self, query):
        # SoundCloud works independently of the YouTube egress block seen on Infrlo.
        try:
            return await self._download_from_soundcloud(query)
        except Exception as exc:
            log.warning("SoundCloud extraction failed: %s", exc)

        try:
            log.info("Trying live Piped extraction.")
            return await self._download_from_piped(query)
        except Exception as exc:
            log.warning("Piped extraction failed: %s", exc)

        d = Path(tempfile.gettempdir()) / "mcord_music"
        d.mkdir(parents=True, exist_ok=True)
        out = d / "%(id)s.%(ext)s"
        target = query if query.startswith(("http://", "https://")) else "ytsearch1:" + query
        ffmpeg_dir = str(Path(self.config.ffmpeg_path).resolve().parent)

        cmd = [
            self.config.ytdlp_path,
            "--no-playlist",
            "--format", "bestaudio/best",
            "--ffmpeg-location", ffmpeg_dir,
            "--retries", "1",
            "--fragment-retries", "1",
            "--socket-timeout", "12",
            "--no-warnings",
            "--print", "after_move:title",
            "--print", "after_move:filepath",
            "--output", str(out),
        ]
        cookies_file = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
        cookies_b64 = os.getenv("YOUTUBE_COOKIES_B64", "").strip()
        if not cookies_file and cookies_b64:
            try:
                cookies_file = str(d / "youtube_cookies.txt")
                Path(cookies_file).write_bytes(
                    base64.b64decode(cookies_b64, validate=True)
                )
                log.info("YouTube cookies loaded from YOUTUBE_COOKIES_B64.")
            except Exception:
                cookies_file = ""
        if cookies_file and Path(cookies_file).is_file():
            cmd += ["--cookies", cookies_file]

        proxy = os.getenv("YOUTUBE_PROXY", "").strip()
        user_agent = os.getenv("YOUTUBE_USER_AGENT", "").strip()
        if proxy:
            cmd += ["--proxy", proxy]
        if user_agent:
            cmd += ["--user-agent", user_agent]
        cmd.append(target)

        try:
            log.info("Trying direct YouTube fallback.")
            p = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await p.communicate()
            if p.returncode == 0:
                lines = [
                    x.strip() for x in stdout.decode("utf-8", "replace").splitlines()
                    if x.strip()
                ]
                if len(lines) >= 2:
                    path = Path(lines[-1])
                    if path.exists():
                        duration = await self._media_duration(path)
                        if duration is not None:
                            self.temp_files.add(path)
                            return Track(lines[-2], path, query=query)

            error = stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(error[-1600:] or "unknown YouTube error")
        except Exception as exc:
            raise RuntimeError(
                "پخش نشد. SoundCloud، Piped و YouTube مستقیم از این سرور پاسخ قابل‌استفاده ندادند. "
                + str(exc)
            ) from exc

    async def _media_duration(self, path: Path) -> float | None:
        """Read the real media duration without blocking the Discord gateway."""
        ffprobe = os.getenv("FFPROBE_PATH", "").strip()
        if not ffprobe:
            candidate = Path(self.config.ffmpeg_path).with_name("ffprobe")
            if candidate.is_file():
                ffprobe = str(candidate)
            else:
                ffprobe = shutil.which("ffprobe") or "ffprobe"

        try:
            process = await asyncio.create_subprocess_exec(
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                log.warning(
                    "ffprobe failed for %s: %s",
                    path,
                    stderr.decode("utf-8", "replace").strip()[-500:],
                )
                return None

            duration = float(stdout.decode("utf-8", "replace").strip())
            if duration <= 0:
                return None
            log.info(
                "Media duration: title=%s duration=%.2fs size=%d",
                self.current.title if self.current else path.name,
                duration,
                path.stat().st_size,
            )
            return duration
        except Exception:
            log.exception("Could not probe media duration: %s", path)
            return None

    async def _wait_for_playback_end(self, path: Path) -> None:
        """Wait for actual media length before trusting mcord_voice idle state.

        mcord_voice can report idle immediately after play() on a fresh
        connection. Waiting for the media duration prevents premature source
        deletion. An explicit skip/stop interrupts this wait immediately.
        """
        duration = await self._media_duration(path)
        if duration is not None:
            interrupt = asyncio.Event()
            self.playback_interrupt = interrupt
            try:
                try:
                    await asyncio.wait_for(interrupt.wait(), timeout=max(0.25, duration + 0.35))
                    return
                except asyncio.TimeoutError:
                    pass
            finally:
                if self.playback_interrupt is interrupt:
                    self.playback_interrupt = None

        # Keep the SDK's own completion check as the final confirmation when
        # possible. At this point the media should have had enough time to
        # finish, so an early idle result is harmless.
        if self.connection is not None:
            try:
                await self.connection.wait_until_idle()
            except Exception:
                log.exception("wait_until_idle failed after duration-based wait")

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

            await self._wait_for_playback_end(t.path)
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

        if self.playback_interrupt is not None:
            self.playback_interrupt.set()

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
            return await self._temp_followup(
                interaction,
                "اول وارد Voice Channel شو."
            )

        lock = self.play_lock(guild_id)
        if lock.locked():
            return await self._temp_followup(
                interaction,
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
                    await self._temp_followup(
                        interaction,
                        f"آهنگ {track.title} به صف اضافه شد. جایگاه: {pos}",
                    )
                except (discord.NotFound, discord.HTTPException) as send_exc:
                    log.warning("Could not send play success message: %s", send_exc)
            except Exception as exc:
                log.exception("play failed")
                try:
                    await self._temp_followup(
                        interaction,
                        f"پخش نشد: {str(exc)[:700]}",
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

    async def _delete_after_delay(self, message, delay: float = 10.0):
        """Delete one bot message after a short delay."""
        try:
            await asyncio.sleep(delay)
            await message.delete()
        except (discord.NotFound, discord.HTTPException, discord.Forbidden):
            pass
        except Exception:
            log.exception("Could not auto-delete bot message.")

    async def _temp_followup(self, interaction, content: str):
        """Send a follow-up message and remove it automatically."""
        message = await interaction.followup.send(content, wait=True)
        asyncio.create_task(self._delete_after_delay(message))
        return message

    async def _temp_response(self, interaction, content: str, **kwargs):
        """Send an initial interaction response and remove it automatically."""
        await interaction.response.send_message(content, **kwargs)
        try:
            message = await interaction.original_response()
            asyncio.create_task(self._delete_after_delay(message))
        except (discord.NotFound, discord.HTTPException):
            pass
        return message if "message" in locals() else None

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
                return await self._temp_response(
                    interaction,
                    "این دستور فقط داخل سرور قابل استفاده است.",
                )

            player = self.player(str(interaction.guild.id))
            await player.set_loop(mode)

            labels = {
                "off": "خاموش شد.",
                "song": "تکرار همین آهنگ فعال شد.",
                "queue": "تکرار کل صف فعال شد.",
            }
            await self._temp_response(
                interaction,
                f"🔁 {labels[mode]}",
            )

        @self.bot.tree.command(name="skip", description="آهنگ بعدی")
        async def skip(interaction):
            await interaction.response.defer()
            player = self.player(str(interaction.guild.id))

            if player.connection is None:
                return await self._temp_followup(
                interaction,
                "چیزی در حال پخش نیست.",
            )

            if player.playback_interrupt is not None:
                player.playback_interrupt.set()

            await player.connection.stop()
            await self._temp_followup(
            interaction,
            "رفتن به آهنگ بعدی.",
        )

        @self.bot.tree.command(name="stop", description="توقف و خروج از Voice")
        async def stop(interaction):
            await interaction.response.defer()
            player = self.player(str(interaction.guild.id))
            await player.stop()
            await player.leave()
            await self._temp_followup(
            interaction,
            "پخش متوقف شد و از Voice خارج شدم.",
        )

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
            await self._temp_followup(
            interaction,
            "پخش مکث شد.",
        )

        @self.bot.tree.command(name="resume", description="ادامه پخش")
        async def resume(interaction):
            await interaction.response.defer()
            player = self.player(str(interaction.guild.id))
            if player.connection is None:
                return await self._temp_followup(
                interaction,
                "چیزی برای ادامه نیست.",
            )
            await player.connection.resume()
            await self._temp_followup(
            interaction,
            "پخش ادامه پیدا کرد.",
        )
