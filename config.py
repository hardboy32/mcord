import os
import shutil
import subprocess
import urllib.request
import zipfile
import platform
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Public Mcord application. Only the bot token is secret.
APPLICATION_ID = "16"
API_BASE = "https://mcord.ir"


def resolve_ffmpeg_path() -> str:
    """Return a working ffmpeg executable, including the portable fallback."""
    configured = os.getenv("FFMPEG_PATH", "").strip()
    if configured:
        return configured

    try:
        from static_ffmpeg import run
        ffmpeg, _ffprobe = run.get_or_fetch_platform_executables_else_raise()
        return str(ffmpeg)
    except Exception:
        return "ffmpeg"


def _download_deno() -> str | None:
    """Download a private, local Deno binary when the host does not provide one."""
    if os.name != "posix" or platform.system().lower() != "linux":
        return None

    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        asset = "deno-x86_64-unknown-linux-gnu.zip"
    elif machine in ("aarch64", "arm64"):
        asset = "deno-aarch64-unknown-linux-gnu.zip"
    else:
        return None

    target_dir = Path.home() / ".local" / "mcord" / "deno"
    binary = target_dir / "deno"
    if binary.exists() and os.access(binary, os.X_OK):
        return str(binary)

    target_dir.mkdir(parents=True, exist_ok=True)
    archive = target_dir / "deno.zip"
    url = f"https://github.com/denoland/deno/releases/latest/download/{asset}"

    try:
        urllib.request.urlretrieve(url, archive)
        with zipfile.ZipFile(archive) as zf:
            zf.extract("deno", target_dir)
        archive.unlink(missing_ok=True)
        binary.chmod(0o755)
        return str(binary)
    except Exception:
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def resolve_deno_path() -> str:
    """Find Deno, installing a local copy on Linux hosts when necessary."""
    configured = os.getenv("DENO_PATH", "").strip()
    if configured and Path(configured).exists():
        return configured

    found = shutil.which("deno")
    if found:
        return found

    candidates = [
        Path.home() / ".deno" / "bin" / "deno",
        Path.home() / ".local" / "mcord" / "deno" / "deno",
        Path("/usr/local/bin/deno"),
        Path("/usr/bin/deno"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    downloaded = _download_deno()
    if downloaded:
        return downloaded

    # Keep the normal yt-dlp diagnostic if Deno cannot be installed.
    return "deno"


BGUTIL_VERSION = "2.0.0"
BGUTIL_URL = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/2.0.0.zip"

def setup_bgutil_provider(deno_path: str) -> str | None:
    base = Path.home() / ".local" / "mcord" / "bgutil-ytdlp-pot-provider"
    server = base / "bgutil-ytdlp-pot-provider-2.0.0" / "server"
    marker = server / "node_modules"
    if not server.exists():
        base.mkdir(parents=True, exist_ok=True)
        archive = base / "bgutil-2.0.0.zip"
        try:
            urllib.request.urlretrieve(BGUTIL_URL, archive)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(base)
            archive.unlink(missing_ok=True)
        except Exception:
            return None
    if not marker.exists():
        try:
            subprocess.run([deno_path, "install", "--allow-scripts=npm:canvas", "--frozen"], cwd=server, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=240)
        except Exception:
            return None
    return str(server)

@dataclass(frozen=True)
class Config:
    bot_token: str
    application_id: str = APPLICATION_ID
    api_base: str = API_BASE
    ytdlp_path: str = "yt-dlp"
    ffmpeg_path: str = "ffmpeg"
    deno_path: str = "deno"
    bgutil_path: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("MCORD_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "MCORD_BOT_TOKEN is not set. Add only the Bot Token as an Infrlo environment variable."
            )

        deno = resolve_deno_path()
        bgutil = setup_bgutil_provider(deno)
        return cls(
            bot_token=token,
            ytdlp_path=os.getenv("YTDLP_PATH", "yt-dlp"),
            ffmpeg_path=resolve_ffmpeg_path(),
            deno_path=deno,
            bgutil_path=bgutil or "",
        )
