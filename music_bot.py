from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from mcord_voice import VoiceClient, create_audio_resource
from music import Track

log = logging.getLogger("mcord-music")


class GuildPlayer:
    def __init__(self, config):
        self.config = config
        self.voice = VoiceClient(token=config.bot_token, api_base=config.api_base)
        self.connection = None
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.temp_files: set[Path] = set()

    async def join(self, guild_id: str, channel_id: str):
        if self.connection is not None:
            if getattr(self.connection, "channel_id", None) == channel_id:
                return
            await self.leave()
        self.connection = await self.voice.join(guild_id=guild_id, channel_id=channel_id)

    async def leave(self):
        if self.connection is not None:
            try:
                await self.connection.leave()
            except Exception:
                log.exception("voice leave failed")
            self.connection = None
        try:
            await self.voice.shutdown()
        except Exception:
            log.exception("voice shutdown failed")
        self.current = None
        for path in list(self.temp_files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self.temp_files.clear()

    async def download(self, query: str) -> Track:
        temp_dir = Path(tempfile.gettempdir()) / "mcord_music"
        temp_dir.mkdir(parents=True, exist_ok=True)
        output = temp_dir / "%(id)s.%(ext)s"
        target = query if query.startswith(("http://", "https://")) else "ytsearch1:" + query
        cmd = [
            self.config.ytdlp_path, "--no-playlist", "--extract-audio",
            "--audio-format", "mp3", "--audio-quality", "5",
            "--print", "after_move:title", "--print", "after_move:filepath",
            "--output", str(output), target,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace")[-1000:])
        lines = [x.strip() for x in stdout.decode("utf-8", "replace").splitlines() if x.strip()]
        if len(lines) < 2:
            raise RuntimeError("yt-dlp audio file was not created.")
        path = Path(lines[-1])
        if not path.exists():
            raise RuntimeError("Downloaded audio file was not found.")
        self.temp_files.add(path)
        return Track(title=lines[-2], path=path)

    async def play_next(self):
        if self.connection is None or not self.queue:
            return
        track = self.queue.pop(0)
        self.current = track
        resource = create_audio_resource(str(track.path), ffmpeg_path=self.config.ffmpeg_path)
        await self.connection.play(resource)
        await self.connection.wait_until_idle()
        try:
            track.path.unlink(missing_ok=True)
        except OSError:
            pass
        self.temp_files.discard(track.path)
        self.current = None
        if self.queue:
            await self.play_next()

    async def add(self, track: Track) -> int:
        self.queue.append(track)
        position = len(self.queue)
        if self.current is None and self.connection is not None:
            asyncio.create_task(self.play_next())
        return position

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
        self.players: dict[str, GuildPlayer] = {}

    def player(self, guild_id: str):
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(self.config)
        return self.players[guild_id]

    def register(self):
        @self.bot.tree.command(name="play", description="پخش آهنگ از نام یا لینک")
        @app_commands.describe(query="نام آهنگ یا لینک")
        async def play(interaction: discord.Interaction, query: str):
            await interaction.response.defer()
            if interaction.guild is None:
                await interaction.followup.send("این دستور فقط داخل سرور قابل استفاده است.")
                return
            channel = getattr(getattr(interaction.user, "voice", None), "channel", None)
            if channel is None:
                await interaction.followup.send("اول وارد Voice Channel شو.")
                return
            player = self.player(str(interaction.guild.id))
            try:
                await player.join(str(interaction.guild.id), str(channel.id))
                track = await player.download(query)
                track.requested_by = str(interaction.user)
                position = await player.add(track)
                await interaction.followup.send(f"آهنگ {track.title} به صف اضافه شد. جایگاه: {position}")
            except Exception as exc:
                log.exception("play failed")
                await interaction.followup.send(f"پخش نشد: {str(exc)[:700]}")

        @self.bot.tree.command(name="skip", description="آهنگ بعدی")
        async def skip(interaction: discord.Interaction):
            await interaction.response.defer()
            if interaction.guild is None:
                await interaction.followup.send("فقط داخل سرور.")
                return
            player = self.player(str(interaction.guild.id))
            if player.connection is None:
                await interaction.followup.send("چیزی در حال پخش نیست.")
                return
            await player.connection.stop()
            await interaction.followup.send("رفتن به آهنگ بعدی.")

        @self.bot.tree.command(name="stop", description="توقف و خروج از Voice")
        async def stop(interaction: discord.Interaction):
            await interaction.response.defer()
            if interaction.guild is None:
                await interaction.followup.send("فقط داخل سرور.")
                return
            player = self.player(str(interaction.guild.id))
            await player.stop()
            await player.leave()
            await interaction.followup.send("پخش متوقف شد و از Voice خارج شدم.")

        @self.bot.tree.command(name="queue", description="نمایش صف")
        async def queue(interaction: discord.Interaction):
            if interaction.guild is None:
                await interaction.response.send_message("فقط داخل سرور.")
                return
            player = self.player(str(interaction.guild.id))
            lines = []
            if player.current:
                lines.append(f"در حال پخش: {player.current.title}")
            lines.extend(f"{i}. {t.title}" for i, t in enumerate(player.queue, 1))
            await interaction.response.send_message(
                "صف خالی است." if not lines else "صف پخش:\n" + "\n".join(lines[:20])
            )

        @self.bot.tree.command(name="pause", description="مکث پخش")
        async def pause(interaction: discord.Interaction):
            await interaction.response.defer()
            if interaction.guild is None:
                await interaction.followup.send("فقط داخل سرور.")
                return
            player = self.player(str(interaction.guild.id))
            if player.connection is None:
                await interaction.followup.send("چیزی در حال پخش نیست.")
                return
            await player.connection.pause()
            await interaction.followup.send("پخش مکث شد.")

        @self.bot.tree.command(name="resume", description="ادامه پخش")
        async def resume(interaction: discord.Interaction):
            await interaction.response.defer()
            if interaction.guild is None:
                await interaction.followup.send("فقط داخل سرور.")
                return
            player = self.player(str(interaction.guild.id))
            if player.connection is None:
                await interaction.followup.send("چیزی برای ادامه نیست.")
                return
            await player.connection.resume()
            await interaction.followup.send("پخش ادامه پیدا کرد.")
