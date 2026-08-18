#!/usr/bin/env python3
"""Thin safety wrapper around yt-dlp for public social-media archiving."""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SCRIPT_VERSION = "2.1.0"
STRATEGY_VERSION = "2026-07-23.1"
INSTALOADER_VERSION = "4.15.2"
OUTPUT_TEMPLATE = "%(uploader_id|unknown)s/%(upload_date>%Y-%m-%d)s_%(title).80B_%(id)s.%(ext)s"
KNOWN_PLATFORMS = {
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
}
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
INSTAGRAM_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{5,32}$")


class ArchiverError(RuntimeError):
    """Expected workflow failure."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_host(host: str) -> str:
    lowered = host.lower().rstrip(".")
    for prefix in ("www.", "m.", "vm.", "vt.", "music."):
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix):]
            break
    return lowered


def validate_public_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url.strip())
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("Only absolute http(s) URLs are accepted")
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".local") or "." not in lowered:
        raise ValueError("Local or unqualified hosts are not accepted")
    try:
        ipaddress.ip_address(lowered)
    except ValueError:
        pass
    else:
        raise ValueError("Literal IP addresses are not accepted")
    base_host = canonical_host(lowered)
    clean_path = parsed.path or "/"
    if base_host == "instagram.com":
        path_segments = [segment for segment in clean_path.split("/") if segment]
        lowered_segments = [segment.lower() for segment in path_segments]
        for kind in ("reel", "p", "tv"):
            if kind in lowered_segments:
                kind_index = lowered_segments.index(kind)
                if kind_index + 1 < len(path_segments):
                    clean_path = f"/{kind}/{path_segments[kind_index + 1]}/"
                break
    if base_host == "youtube.com":
        allowed_query_keys = {"v", "list", "index"}
        clean_query = urlencode([(key, value) for key, value in parse_qsl(parsed.query) if key in allowed_query_keys])
    elif base_host in {"instagram.com", "tiktok.com", "youtu.be"}:
        clean_query = ""
    else:
        clean_query = parsed.query
    return urlunsplit(("https", lowered, clean_path, clean_query, ""))


def classify_source(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    host = canonical_host(parsed.hostname or "")
    platform = KNOWN_PLATFORMS.get(host, "other")
    segments = [segment for segment in parsed.path.split("/") if segment]

    if platform == "instagram":
        lowered_segments = [segment.lower() for segment in segments]
        source_type = "video" if any(kind in lowered_segments[:-1] for kind in {"p", "reel", "tv"}) else "profile"
    elif platform == "tiktok":
        source_type = "video" if "video" in [segment.lower() for segment in segments] or host == "tiktok.com" and parsed.hostname and parsed.hostname.lower().startswith(("vm.", "vt.")) else "profile"
    elif platform == "youtube":
        direct_roots = {"watch", "shorts", "live", "embed"}
        source_type = "video" if host == "youtu.be" or bool(segments and segments[0].lower() in direct_roots) else "profile"
    else:
        source_type = "auto"
    return {"platform": platform, "source_type": source_type, "host": host}


def instagram_username(value: str) -> str:
    candidate = value.strip().lstrip("@").rstrip("/")
    if candidate.startswith(("http://", "https://")):
        clean = validate_public_url(candidate)
        source = classify_source(clean)
        if source["platform"] != "instagram" or source["source_type"] != "profile":
            raise ValueError("Expected an Instagram profile URL or username")
        segments = [segment for segment in urlsplit(clean).path.split("/") if segment]
        if len(segments) != 1:
            raise ValueError("Expected an Instagram profile URL or username")
        candidate = segments[0]
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", candidate):
        raise ValueError("Invalid Instagram username")
    return candidate


def instagram_code_from_url(url: str) -> str | None:
    clean = validate_public_url(url)
    source = classify_source(clean)
    if source["platform"] != "instagram" or source["source_type"] != "video":
        return None
    segments = [segment for segment in urlsplit(clean).path.split("/") if segment]
    return segments[1] if len(segments) >= 2 else None


def load_known_instagram_codes(paths: list[Path]) -> set[str]:
    codes: set[str] = set()
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Known-items file not found: {path}")
        text = path.read_text(encoding="utf-8")
        for url in extract_discovered_urls(text):
            code = instagram_code_from_url(url)
            if code:
                codes.add(code)
        for line in text.splitlines():
            fields = line.split("\t")
            candidates = fields[2:3] if len(fields) >= 3 else fields[-1:]
            for candidate in candidates:
                stripped = candidate.strip()
                if INSTAGRAM_CODE_PATTERN.fullmatch(stripped):
                    codes.add(stripped)
    return codes


def select_new_instagram_reels(
    posts: Any,
    known_codes: set[str],
    max_items: int,
    stop_on_known: bool,
) -> tuple[list[str], str | None]:
    urls: list[str] = []
    seen: set[str] = set()
    stopped_at: str | None = None
    for post in posts:
        code = str(getattr(post, "shortcode", ""))
        if not INSTAGRAM_CODE_PATTERN.fullmatch(code) or code in seen:
            continue
        seen.add(code)
        if code in known_codes:
            if stop_on_known:
                stopped_at = code
                break
            continue
        urls.append(f"https://www.instagram.com/reel/{code}/")
        if len(urls) >= max_items:
            break
    return urls, stopped_at


def instaloader_discovery_options(request_timeout: int) -> dict[str, Any]:
    return {
        "sleep": True,
        "quiet": True,
        "download_pictures": False,
        "download_videos": False,
        "download_video_thumbnails": False,
        "save_metadata": False,
        "compress_json": False,
        "max_connection_attempts": 1,
        "request_timeout": float(request_timeout),
        "fatal_status_codes": [302, 400, 401, 403, 429],
        "iphone_support": False,
    }


def extract_discovered_urls(text: str, instagram_profile: str | None = None) -> list[str]:
    """Normalize URL/code output from any browser agent into canonical URLs."""
    candidates: list[str] = []
    for match in URL_PATTERN.findall(text):
        candidates.append(match.rstrip(")]},.;"))

    if instagram_profile:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = None

        codes: list[str] = []
        if isinstance(value, list):
            codes.extend(str(item) for item in value if isinstance(item, str))
        elif isinstance(value, dict):
            for key in ("codes", "shortcodes"):
                items = value.get(key, [])
                if isinstance(items, list):
                    codes.extend(str(item) for item in items if isinstance(item, str))
        else:
            for line in text.splitlines():
                stripped = line.strip()
                if INSTAGRAM_CODE_PATTERN.fullmatch(stripped):
                    codes.append(stripped)
        candidates.extend(f"https://www.instagram.com/reel/{code}/" for code in codes if INSTAGRAM_CODE_PATTERN.fullmatch(code))

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            clean = validate_public_url(candidate)
        except ValueError:
            continue
        if clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized


def read_sources_file(path: Path) -> list[str]:
    if not path.is_file():
        raise ValueError(f"Sources file not found: {path}")
    return extract_discovered_urls(path.read_text(encoding="utf-8"))


def atomic_write_text(destination: Path, text: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)


def classify_failure(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("429", "too many requests", "rate limit", "ratelimit")):
        return "rate_limit"
    if any(token in lowered for token in ("checkpoint", "challenge_required", "captcha", "login required", "please log in", "sign in to confirm")):
        return "challenge"
    if any(token in lowered for token in ("private", "not available", "not found", "404", "removed")):
        return "not_found"
    if any(token in lowered for token in ("timed out", "timeout", "temporarily", "connection", "http error 5", "failed to fetch", "ssl", "unexpected_eof", "eof occurred")):
        return "transient"
    return "unknown"


def cooldown_seconds(category: str) -> int:
    return {
        "rate_limit": 3600,
        "challenge": 86400,
        "not_found": 86400,
        "transient": 900,
    }.get(category, 900)


def require_binary(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise ArchiverError(f"Required command not found: {name}")
    return executable


def run_capture(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def atomic_write_json(destination: Path, value: Any) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)


def load_state(state_path: Path) -> dict[str, Any]:
    try:
        with state_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {"version": 1, "circuit_open_until": 0, "source_cooldowns": {}, "runs": []}
    except json.JSONDecodeError as exc:
        raise ArchiverError(f"Invalid state file: {state_path}: {exc}") from exc


def save_run(state_path: Path, state: dict[str, Any], run: dict[str, Any]) -> None:
    state.setdefault("runs", []).append(run)
    state["runs"] = state["runs"][-100:]
    state["updated_at"] = now_iso()
    atomic_write_json(state_path, state)


def build_ytdlp_command(args: argparse.Namespace, sources: list[dict[str, Any]]) -> list[str]:
    yt_dlp = require_binary("yt-dlp")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    has_collection = any(source["source_type"] in {"profile", "auto"} for source in sources)

    command = [
        yt_dlp,
        "--ignore-config",
        "--no-cookies",
        "--abort-on-error",
        "--skip-playlist-after-errors", "1",
        "--retries", "0",
        "--fragment-retries", "0",
        "--extractor-retries", "0",
        "--file-access-retries", "0",
        "--socket-timeout", str(args.socket_timeout),
        "--concurrent-fragments", "1",
        "--sleep-requests", str(args.sleep_requests),
        "--download-archive", str(output_dir / ".download-archive.txt"),
        "--break-per-input",
        "--write-info-json",
        "--clean-info-json",
        "--no-write-playlist-metafiles",
        "--no-overwrites",
        "--no-mtime",
        "--trim-filenames", "180",
        "--format", "best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--paths", str(output_dir),
        "--output", OUTPUT_TEMPLATE,
        "--print", "after_move:filepath=%(filepath)s",
    ]
    if has_collection:
        command.extend([
            "--yes-playlist",
            "--lazy-playlist",
            "--playlist-items", f":{args.max_items}",
            "--break-on-existing",
            "--sleep-interval", str(args.min_sleep),
            "--max-sleep-interval", str(args.max_sleep),
        ])
    else:
        command.append("--no-playlist")
    if args.dry_run:
        command.append("--simulate")
    command.extend(source["url"] for source in sources)
    return command


def verify_file(file_path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"file": str(file_path), "verified": False, "reason": "ffprobe not installed"}
    result = run_capture([
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height",
        "-of", "json",
        str(file_path),
    ], 30)
    if result.returncode != 0:
        return {"file": str(file_path), "verified": False, "reason": result.stderr.strip()}
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    size = int(data.get("format", {}).get("size", 0) or 0)
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    return {
        "file": str(file_path),
        "verified": bool(has_video and duration > 0 and size > 0),
        "duration": duration,
        "size": size,
        "streams": streams,
    }


def command_archive(args: argparse.Namespace) -> int:
    if not 1 <= args.max_items <= 100:
        raise ValueError("--max-items must be between 1 and 100")
    if args.min_sleep < 5 or args.max_sleep < args.min_sleep:
        raise ValueError("Profile delays require 5 <= --min-sleep <= --max-sleep")

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    if args.source_offset < 0:
        raise ValueError("--source-offset must be zero or greater")
    raw_sources = list(args.sources)
    file_sources: list[str] = []
    for sources_file in args.sources_file:
        file_sources.extend(read_sources_file(sources_file))
    raw_sources.extend(file_sources[args.source_offset:args.source_offset + args.max_items])
    if not raw_sources:
        raise ValueError("Provide at least one URL or --sources-file")
    for raw_source in raw_sources:
        clean_url = validate_public_url(raw_source)
        if clean_url in seen:
            continue
        seen.add(clean_url)
        source = {"url": clean_url, **classify_source(clean_url)}
        if source["platform"] == "instagram" and source["source_type"] == "profile":
            raise ValueError(
                "Instagram profile enumeration is not supported by the yt-dlp path; "
                "run discover-instagram first, then archive --sources-file"
            )
        sources.append(source)

    output_dir = Path(args.output_dir)
    state_path = output_dir / ".safe-social-archive-state.json"
    state = load_state(state_path)
    current_time = time.time()
    if float(state.get("circuit_open_until", 0) or 0) > current_time:
        raise ArchiverError(f"Safety circuit is open until epoch {state['circuit_open_until']:.0f}")
    source_cooldowns = state.setdefault("source_cooldowns", {})
    for source in sources:
        next_allowed = float(source_cooldowns.get(source["url"], 0) or 0)
        if next_allowed > current_time:
            raise ArchiverError(f"Source cooldown is active until epoch {next_allowed:.0f}: {source['url']}")

    command = build_ytdlp_command(args, sources)
    started = time.monotonic()
    result = run_capture(command, args.command_timeout)
    elapsed = round(time.monotonic() - started, 3)
    combined_error = "\n".join(part for part in (result.stderr, result.stdout) if part)
    category = classify_failure(combined_error) if result.returncode else None
    files = [Path(line.removeprefix("filepath=")) for line in result.stdout.splitlines() if line.startswith("filepath=")]
    verifications = [] if args.dry_run else [verify_file(file_path) for file_path in files if file_path.exists()]

    run_record: dict[str, Any] = {
        "started_at": now_iso(),
        "elapsed_seconds": elapsed,
        "sources": sources,
        "max_items": args.max_items,
        "dry_run": args.dry_run,
        "returncode": result.returncode,
        "failure_category": category,
        "files": [str(file_path) for file_path in files],
    }
    if category:
        cooldown = cooldown_seconds(category)
        for source in sources:
            source_cooldowns[source["url"]] = time.time() + cooldown
        run_record["source_cooldown_seconds"] = cooldown
    else:
        for source in sources:
            source_cooldowns.pop(source["url"], None)
    if category in {"rate_limit", "challenge"}:
        state["circuit_open_until"] = time.time() + cooldown
        run_record["circuit_open_seconds"] = cooldown
    save_run(state_path, state, run_record)

    summary = {
        "status": "ok" if result.returncode == 0 else "error",
        "elapsed_seconds": elapsed,
        "sources": sources,
        "downloaded_files": len(files),
        "verified_files": sum(item.get("verified") is True for item in verifications),
        "verifications": verifications,
        "failure_category": category,
    }
    if result.returncode != 0:
        summary["error"] = combined_error[-2000:]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.returncode == 0 and all(item.get("verified") for item in verifications) else 1


def command_normalize_discovery(args: argparse.Namespace) -> int:
    chunks: list[str] = []
    for input_path in args.inputs:
        if str(input_path) == "-":
            chunks.append(sys.stdin.read())
        else:
            chunks.append(input_path.read_text(encoding="utf-8"))
    urls = extract_discovered_urls("\n".join(chunks), args.instagram_profile)
    output = "\n".join(urls) + ("\n" if urls else "")
    if args.output:
        atomic_write_text(args.output, output)
    else:
        sys.stdout.write(output)
    summary = {"discovered": len(urls), "output": str(args.output) if args.output else "stdout"}
    print(json.dumps(summary, ensure_ascii=False), file=sys.stderr)
    return 0 if urls else 1


def command_discover_instagram(args: argparse.Namespace) -> int:
    if not 1 <= args.max_items <= 100:
        raise ValueError("--max-items must be between 1 and 100")
    if not 5 <= args.request_timeout <= 60:
        raise ValueError("--request-timeout must be between 5 and 60 seconds")
    username = instagram_username(args.profile)
    known_codes = load_known_instagram_codes(args.known)
    try:
        import instaloader
    except ImportError as exc:
        raise ArchiverError(
            "Optional dependency missing: instaloader. Run this command with "
            f"'uv run --with instaloader=={INSTALOADER_VERSION} python scripts/safe_social_archiver.py ...'"
        ) from exc

    loader = instaloader.Instaloader(**instaloader_discovery_options(args.request_timeout))
    try:
        profile = instaloader.Profile.from_username(loader.context, username)
        urls, stopped_at = select_new_instagram_reels(
            profile.get_reels(),
            known_codes,
            args.max_items,
            stop_on_known=bool(known_codes and not args.scan_past_known),
        )
    except Exception as exc:
        category = classify_failure(str(exc))
        raise ArchiverError(f"Instagram discovery stopped ({category}): {str(exc)[:500]}") from exc

    output = "\n".join(urls) + ("\n" if urls else "")
    if args.output:
        atomic_write_text(args.output, output)
    else:
        sys.stdout.write(output)
    summary = {
        "profile": username,
        "discovered": len(urls),
        "known_codes": len(known_codes),
        "stopped_at_known": stopped_at,
        "authenticated": False,
        "media_downloaded": False,
        "output": str(args.output) if args.output else "stdout",
    }
    print(json.dumps(summary, ensure_ascii=False), file=sys.stderr)
    return 0


def extractor_capabilities(names: set[str]) -> dict[str, bool]:
    lowered = {
        name.lower().strip()
        for name in names
        if "currently broken" not in name.lower()
    }
    return {
        "instagram_single": "instagram" in lowered,
        "instagram_profile": any(
            name.startswith(("instagram:user", "instagramuser")) for name in lowered
        ),
        "tiktok": any(name.startswith("tiktok") for name in lowered),
        "youtube_channel": "youtube:tab" in lowered,
    }


def check_latest_release() -> dict[str, Any]:
    current_result = run_capture([require_binary("yt-dlp"), "--version"], 15)
    current = current_result.stdout.strip()
    request = urllib.request.Request(
        "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"SuperDown88/{SCRIPT_VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            release = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"current": current, "latest": None, "check_error": str(exc)}
    latest = str(release.get("tag_name", "")).lstrip("v")
    return {"current": current, "latest": latest, "update_available": bool(latest and latest != current)}


def command_doctor(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "script_version": SCRIPT_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "python": sys.version.split()[0],
        "yt_dlp": None,
        "ffprobe": None,
        "supported_extractors": None,
        "optional_discovery": {
            "instaloader": {
                "installed": importlib.util.find_spec("instaloader") is not None,
                "verified_version": INSTALOADER_VERSION,
            }
        },
    }
    yt_dlp = shutil.which("yt-dlp")
    if yt_dlp:
        version = run_capture([yt_dlp, "--version"], 15).stdout.strip()
        extractors = run_capture([yt_dlp, "--ignore-config", "--list-extractors"], 30)
        names = set(extractors.stdout.splitlines())
        report["yt_dlp"] = {"path": yt_dlp, "version": version}
        report["supported_extractors"] = extractor_capabilities(names)
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        first_line = run_capture([ffprobe, "-version"], 15).stdout.splitlines()
        report["ffprobe"] = {"path": ffprobe, "version": first_line[0] if first_line else "unknown"}
    if args.check_updates and yt_dlp:
        report["update"] = check_latest_release()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if yt_dlp else 2


def command_update(args: argparse.Namespace) -> int:
    report = check_latest_release()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply or not report.get("update_available"):
        return 0
    yt_dlp = Path(require_binary("yt-dlp")).resolve()
    if shutil.which("brew") and ("/Cellar/" in str(yt_dlp) or "/homebrew/" in str(yt_dlp).lower()):
        command = [require_binary("brew"), "upgrade", "yt-dlp"]
    else:
        command = [str(yt_dlp), "--update"]
    return subprocess.run(command, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {SCRIPT_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check dependencies, extractors, and optional stable updates")
    doctor.add_argument("--check-updates", action="store_true")
    doctor.set_defaults(handler=command_doctor)

    normalize = subparsers.add_parser(
        "normalize-discovery",
        help="Normalize URL/shortcode output from a browser agent into a canonical URL list",
    )
    normalize.add_argument("inputs", nargs="+", type=Path, help="Text/JSON files; use - for stdin")
    normalize.add_argument("--instagram-profile", help="Allow JSON or one-per-line Instagram shortcodes")
    normalize.add_argument("--output", type=Path, help="Atomically write one canonical URL per line")
    normalize.set_defaults(handler=command_normalize_discovery)

    discover_instagram = subparsers.add_parser(
        "discover-instagram",
        help="Discover a bounded set of public Reel URLs without login or media transfer",
    )
    discover_instagram.add_argument("profile", help="Instagram profile URL or username")
    discover_instagram.add_argument("--max-items", type=int, default=5)
    discover_instagram.add_argument("--known", action="append", type=Path, default=[], help="metadata/URL file containing known shortcodes")
    discover_instagram.add_argument("--scan-past-known", action="store_true", help="Backfill past known items instead of stopping at the first known shortcode")
    discover_instagram.add_argument("--request-timeout", type=int, default=20)
    discover_instagram.add_argument("--output", type=Path, help="Atomically write one canonical Reel URL per line")
    discover_instagram.set_defaults(handler=command_discover_instagram)

    archive = subparsers.add_parser("archive", help="Archive one or more public video/profile/channel URLs")
    archive.add_argument("sources", nargs="*")
    archive.add_argument("--sources-file", action="append", type=Path, default=[], help="Read canonical URLs from a text file")
    archive.add_argument("--source-offset", type=int, default=0, help="Zero-based offset for a bounded sources-file batch")
    archive.add_argument("--output-dir", required=True)
    archive.add_argument("--max-items", type=int, default=20)
    archive.add_argument("--sleep-requests", type=float, default=1.0)
    archive.add_argument("--min-sleep", type=float, default=12.0)
    archive.add_argument("--max-sleep", type=float, default=18.0)
    archive.add_argument("--socket-timeout", type=int, default=20)
    archive.add_argument("--command-timeout", type=int, default=7200)
    archive.add_argument("--dry-run", action="store_true")
    archive.set_defaults(handler=command_archive)

    update = subparsers.add_parser("update-tool", help="Check stable yt-dlp; apply only with explicit approval")
    update.add_argument("--apply", action="store_true")
    update.set_defaults(handler=command_update)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.handler(args))
    except (ArchiverError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
