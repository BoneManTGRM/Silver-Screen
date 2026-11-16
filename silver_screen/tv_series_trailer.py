"""Cut season trailers from archived episode footage with FFmpeg."""

from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import fill

from .ai_video import VideoGenerationError
from .direct_generation import FILM_CANVASES, _clip_timing, _encode_video
from .voice_audio import _has_audio


TRAILER_LENGTHS = (15, 30)
TRAILER_FORMATS = {
    "Widescreen": FILM_CANVASES["Landscape HD · 1280 × 720"],
    "Vertical": FILM_CANVASES["Portrait HD · 720 × 1280"],
}
FPS = 24


def _montage(sources: list[tuple[Path, int]], seconds: int, shot_seconds: int) -> list[tuple[Path, float, float]]:
    """Spread distinct cuts across episodes and leave out each closing shot."""
    available = [round(_clip_timing(path)[0] / shot_seconds) - 1 for path, _ in sources]
    available = [max(0, min(count, length // shot_seconds - 1)) for count, (_, length) in zip(available, sources)]
    cut_count = min(sum(available), max(len(sources), seconds // 3))
    if not cut_count or seconds > cut_count * shot_seconds:
        raise VideoGenerationError(f"Select more finished episodes for a {seconds}-second trailer.")

    order = []
    assigned = [0] * len(sources)
    for round_number in range(max(available)):
        for index, count in enumerate(available):
            if round_number < count and len(order) < cut_count:
                order.append((index, assigned[index]))
                assigned[index] += 1

    frames, remainder = divmod(seconds * FPS, cut_count)
    montage = []
    for rank, (index, selection) in enumerate(order):
        scene = selection * (available[index] - 1) // max(1, assigned[index] - 1)
        length = (frames + (rank < remainder)) / FPS
        start = scene * shot_seconds + (shot_seconds - length) / 2
        montage.append((sources[index][0], start, length))
    return montage


def export_trailer(
    sources: list[tuple[Path, int]], destination: Path, *, seconds: int, shot_seconds: int,
    frame_format: str, title: str, season_number: int, tagline: str,
) -> Path:
    if seconds not in TRAILER_LENGTHS or frame_format not in TRAILER_FORMATS or not sources:
        raise VideoGenerationError("Choose finished episodes, a trailer length, and a frame format.")
    shots = _montage(sources, seconds, shot_seconds)
    width, height = TRAILER_FORMATS[frame_format]
    resize = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x10131a,setsar=1,fps={FPS}"
    )
    audio = {path: _has_audio(path) for path, _ in sources}
    with TemporaryDirectory(prefix="trailer-edit-", dir=destination.parent) as directory:
        work = Path(directory)
        cut_files = []
        for number, (path, start, length) in enumerate(shots):
            filename = f"cut-{number:02d}.mp4"
            inputs = ["-ss", f"{start:.6f}", "-i", str(path.resolve())]
            if not audio[path]:
                inputs.extend(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
            audio_stream = "0:a:0" if audio[path] else "1:a:0"
            _encode_video([
                *inputs, "-map", "0:v:0", "-map", audio_stream, "-vf", resize,
                "-af", f"apad,afade=t=in:d=0.04,afade=t=out:st={length - 0.05:.4f}:d=0.05",
                "-t", f"{length:.6f}", "-c:v", "libx264", "-preset", "fast", "-crf", "17",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", filename,
            ], cwd=work)
            cut_files.append(filename)

        (work / "montage.txt").write_text("\n".join(f"file {filename}" for filename in cut_files), encoding="utf-8")
        wrapped_title = fill(title, width=24)
        (work / "title.txt").write_text(wrapped_title, encoding="utf-8")
        (work / "season.txt").write_text(f"SEASON {season_number}", encoding="utf-8")
        if tagline:
            (work / "tagline.txt").write_text(fill(tagline, width=34), encoding="utf-8")

        enable = f"enable='gte(t,{seconds - 3})'"
        filters = [f"fps={FPS},tpad=stop_mode=clone:stop_duration=0.15",
                   f"drawbox=color=0x10131a@0.75:t=fill:{enable}"]
        title_lines = len(wrapped_title.splitlines())
        title_size = min(width // 20, height // 14, (height // 4 - 8 * (title_lines - 1)) // title_lines)
        lines = [("season", min(width // 34, height // 29), "h*0.29", "0xf2c583"),
                 ("title", title_size, "(h-th)/2", "white")]
        if tagline:
            lines.append(("tagline", min(width // 32, height // 24), "h*0.68", "white"))
        for key, size, y, color in lines:
            filters.append(
                f"drawtext=font=DejaVu Sans:textfile={key}.txt:expansion=none:fontcolor={color}:"
                f"fontsize={size}:x=(w-tw)/2:y={y}:line_spacing=8:{enable}"
            )
        staged = work / "trailer.mp4"
        _encode_video([
            "-f", "concat", "-i", "montage.txt", "-map", "0:v:0", "-map", "0:a:0",
            "-vf", ",".join(filters), "-af", f"aresample=async=1:first_pts=0,apad,afade=t=out:st={seconds - 0.25}:d=0.25",
            "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", staged.name,
        ], cwd=work)
        duration, _ = _clip_timing(staged)
        if abs(duration - seconds) > 1 / FPS + 0.001:
            raise VideoGenerationError("The edited trailer does not match the chosen length.")
        staged.replace(destination)
    return destination
