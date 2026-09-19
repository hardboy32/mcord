from __future__ import annotations
import asyncio
import logging
import tempfile
from pathlib import Path
from dataclasses import dataclass
import discord
from discord import app_commands
from mcord_voice import VoiceClient, create_audio_resource

log = logging.getLogger("mcord-music")


@dataclass
class Track:
    title: str
    path: Path
    requested_by: str = "unknown"


class GuildPlayer:
    def __init__(self, config):
        self.config = config
        self.voice = None
        self.connection = None
        self.queue = []
        self.current = None
        self.temp_files = set()

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

        if self.voice is None:
            self._new_voice_client()

        self.connection = await self.voice.join(
            guild_id=guild_id,
            channel_id=channel_id,
        )

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
        for p in list(self.temp_files):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        self.temp_files.clear()

    async def download(self, query):
        d = Path(tempfile.gettempdir()) / "mcord_music"
        d.mkdir(parents=True, exist_ok=True)
        out = d / "%(id)s.%(ext)s"
        target = query if query.startswith(("http://", "https://")) else "ytsearch1:" + query

        ffmpeg_dir = str(Path(self.config.ffmpeg_path).resolve().parent)

        # Prefer clients that currently avoid the most common PO-token path.
        # android_vr does not require a PO token for GVS; web_embedded is also
        # token-free but only works for embeddable videos. Keep the normal
        # extractor as a final fallback because YouTube changes these rules.
        client_variants = ["android_vr", "web_embedded", None]
        errors = []

        for client in client_variants:
            cmd = [
                self.config.ytdlp_path,
                "--no-playlist",
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "5",
                "--ffmpeg-location", ffmpeg_dir,
                "--js-runtimes", f"deno:{self.config.deno_path}",
                "--remote-components", "ejs:npm",
                "--print", "after_move:title",
                "--print", "after_move:filepath",
                "--output", str(out),
            ]
            if client:
                cmd += ["--extractor-args", f"youtube:player_client={client}"]
            cmd.append(target)

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
                        return Track(lines[-2], path)

            error = stderr.decode("utf-8", "replace").strip()
            errors.append(f"{client or 'default'}: {error[-1200:]}")

        raise RuntimeError("YouTube extraction failed. " + " | ".join(errors))

    async def play_next(self):
        if self.connection is None or not self.queue:
            return

        t = self.queue.pop(0)
        self.current = t

        try:
            resource = create_audio_resource(
                str(t.path),
                ffmpeg_path=self.config.ffmpeg_path,
            )
            await self.connection.play(resource)
            await self.connection.wait_until_idle()
        finally:
            try:
                t.path.unlink(missing_ok=True)
            except OSError:
                pass
            self.temp_files.discard(t.path)
            self.current = None

        if self.queue:
            await self.play_next()

    async def add(self, t):
        self.queue.append(t)
        pos = len(self.queue)

        if self.current is None and self.connection is not None:
            asyncio.create_task(self.play_next())

        return pos

    async def stop(self):
        self.queue.clear()

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

    def player(self, guild_id):
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(self.config)
        return self.players[guild_id]

    def register(self):
        @self.bot.tree.command(name="play", description="پخش آهنگ از نام یا لینک")
        @app_commands.describe(query="نام آهنگ یا لینک")
        async def play(interaction: discord.Interaction, query: str):
            await interaction.response.defer()

            if interaction.guild is None:
                return await interaction.followup.send(
                    "این دستور فقط داخل سرور قابل استفاده است."
                )

            channel = getattr(
                getattr(interaction.user, "voice", None),
                "channel",
                None,
            )
            if channel is None:
                return await interaction.followup.send(
                    "اول وارد Voice Channel شو."
                )

            player = self.player(str(interaction.guild.id))

            try:
                track = await player.download(query)
                track.requested_by = str(interaction.user)

                await player.join(
                    str(interaction.guild.id),
                    str(channel.id),
                )

                pos = await player.add(track)
                await interaction.followup.send(
                    f"آهنگ {track.title} به صف اضافه شد. جایگاه: {pos}"
                )
            except Exception as exc:
                log.exception("play failed")
                await interaction.followup.send(
                    f"پخش نشد: {str(exc)[:700]}"
                )

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
