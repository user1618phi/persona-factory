"""YouTube transcript extraction and grounded briefing generation."""

from __future__ import annotations

import asyncio
import html
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from src.config import get_settings
from src.orchestrator.llm_router import generate_text
from src.orchestrator.transcriber import transcribe_audio

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/[^\s<>\"]+",
    re.IGNORECASE,
)
TIMESTAMP_RE = re.compile(
    r"(?P<start>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
    r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})"
)
HTML_TAG_RE = re.compile(r"<[^>]+>")


class YouTubeIngestError(RuntimeError):
    """Raised when a YouTube video cannot be turned into grounded text."""


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class VideoTranscript:
    url: str
    title: str
    duration: int | None
    source: str
    language: str | None
    segments: list[TranscriptSegment]

    @property
    def plain_text(self) -> str:
        return "\n".join(
            f"[{format_timestamp(segment.start)}] {segment.text}"
            for segment in self.segments
            if segment.text
        )


@dataclass(frozen=True)
class YouTubeBriefing:
    url: str
    title: str
    duration: int | None
    transcript_source: str
    language: str | None
    briefing: str
    user_takeaway: str
    processed_chunks: int = 0
    total_chunks: int = 0
    coverage: float = 0.0


@dataclass(frozen=True)
class TranscriptSummary:
    briefing: str
    processed_chunks: int
    total_chunks: int
    coverage: float


@dataclass(frozen=True)
class YouTubeEditorResult:
    editor_input: str
    mode_inputs: dict[str, str]
    url: str
    title: str
    duration: int | None
    transcript_source: str
    language: str | None
    processed_chunks: int
    total_chunks: int
    coverage: float


def extract_youtube_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in YOUTUBE_URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(").,;!?")
        if is_youtube_url(url) and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def strip_youtube_urls(text: str) -> str:
    cleaned = YOUTUBE_URL_RE.sub("", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def is_youtube_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    return host in YOUTUBE_HOSTS


def extract_video_id(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = parsed.netloc.lower()
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
        return video_id or None
    if host not in YOUTUBE_HOSTS:
        return None
    query_id = parse_qs(parsed.query).get("v", [""])[0]
    if query_id:
        return query_id
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
        return parts[1]
    return None


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _parse_timestamp(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    else:
        hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _clean_caption_text(text: str) -> str:
    text = html.unescape(text)
    text = HTML_TAG_RE.sub("", text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -")


def _normalize_for_dedupe(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _overlap_suffix_words(previous: str, current: str) -> int:
    previous_words = previous.split()
    current_words = current.split()
    max_size = min(len(previous_words), len(current_words))
    for size in range(max_size, 2, -1):
        if previous_words[-size:] == current_words[:size]:
            return size
    return 0


def _dedupe_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    deduped: list[TranscriptSegment] = []
    for segment in segments:
        text = _clean_caption_text(segment.text)
        if not text:
            continue
        normalized = _normalize_for_dedupe(text)
        if deduped:
            previous = deduped[-1]
            previous_normalized = _normalize_for_dedupe(previous.text)
            if normalized == previous_normalized or normalized in previous_normalized:
                continue
            if previous_normalized in normalized and segment.start - previous.start <= 5:
                deduped[-1] = TranscriptSegment(
                    start=previous.start,
                    end=segment.end,
                    text=text,
                )
                continue
            overlap = _overlap_suffix_words(previous_normalized, normalized)
            if overlap:
                words = text.split()
                text = " ".join(words[overlap:]).strip()
                if not text:
                    continue
                normalized = _normalize_for_dedupe(text)
            if normalized == previous_normalized:
                continue
        deduped.append(
            TranscriptSegment(start=segment.start, end=segment.end, text=text)
        )
    return deduped


def parse_vtt(text: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    start: float | None = None
    end: float | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal start, end, buffer
        if start is None or end is None:
            buffer = []
            return
        body = _clean_caption_text(" ".join(buffer))
        if body and (not segments or segments[-1].text != body):
            segments.append(TranscriptSegment(start=start, end=end, text=body))
        start = None
        end = None
        buffer = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        timestamp = TIMESTAMP_RE.search(line)
        if timestamp:
            flush()
            start = _parse_timestamp(timestamp.group("start"))
            end = _parse_timestamp(timestamp.group("end"))
            continue
        if "-->" in line or line.isdigit():
            continue
        if start is not None:
            buffer.append(line)
    flush()
    return _dedupe_segments(segments)


def parse_json3(text: str) -> list[TranscriptSegment]:
    data = json.loads(text)
    segments: list[TranscriptSegment] = []
    for event in data.get("events", []):
        pieces = event.get("segs") or []
        body = _clean_caption_text("".join(piece.get("utf8", "") for piece in pieces))
        if not body:
            continue
        start = float(event.get("tStartMs") or 0) / 1000
        duration = float(event.get("dDurationMs") or 0) / 1000
        end = start + duration if duration else start
        if not segments or segments[-1].text != body:
            segments.append(TranscriptSegment(start=start, end=end, text=body))
    return _dedupe_segments(segments)


def _parse_supadata_content(content: list[dict[str, Any]]) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for item in content:
        body = _clean_caption_text(str(item.get("text") or ""))
        if not body:
            continue
        try:
            start = float(item.get("offset") or item.get("start") or 0) / 1000
        except (TypeError, ValueError):
            start = 0.0
        try:
            duration = float(item.get("duration") or 0) / 1000
        except (TypeError, ValueError):
            duration = 0.0
        segments.append(
            TranscriptSegment(
                start=start,
                end=start + duration if duration else start,
                text=body,
            )
        )
    return _dedupe_segments(segments)


def _preferred_languages() -> list[str]:
    configured = get_settings().youtube_caption_languages
    languages = [item.strip() for item in configured.split(",") if item.strip()]
    return languages or ["ru", "en"]


def _choose_caption_track(
    info: dict[str, Any],
) -> tuple[dict[str, Any], str, str] | None:
    languages = _preferred_languages()
    sources = (
        ("manual_captions", "subtitles"),
        ("auto_captions", "automatic_captions"),
    )
    for source_name, key in sources:
        tracks = info.get(key) or {}
        for language in languages + sorted(set(tracks) - set(languages)):
            formats = tracks.get(language) or []
            for wanted_ext in ("vtt", "json3"):
                for track in formats:
                    if track.get("ext") == wanted_ext and track.get("url"):
                        return track, source_name, language
    return None


async def _download_caption(track: dict[str, Any]) -> str:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(track["url"])
        response.raise_for_status()
        return response.text


async def _fetch_supadata_transcript(url: str) -> VideoTranscript | None:
    settings = get_settings()
    if not settings.supadata_api_key:
        return None
    video_id = extract_video_id(url)
    if not video_id:
        return None
    headers = {"x-api-key": settings.supadata_api_key}
    languages = _preferred_languages()
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        title = "YouTube video"
        duration: int | None = None
        try:
            metadata_response = await client.get(
                "https://api.supadata.ai/v1/youtube/video",
                params={"id": video_id},
                headers=headers,
            )
            if metadata_response.status_code == 200:
                metadata = metadata_response.json()
                title = metadata.get("title") or title
                raw_duration = metadata.get("duration")
                if isinstance(raw_duration, (int, float)):
                    duration = int(raw_duration)
        except Exception:
            pass

        last_error: Exception | None = None
        for language in languages:
            try:
                response = await client.get(
                    "https://api.supadata.ai/v1/youtube/transcript",
                    params={"videoId": video_id, "lang": language},
                    headers=headers,
                )
                if response.status_code in {400, 404}:
                    continue
                response.raise_for_status()
                data = response.json()
                content = data.get("content") if isinstance(data, dict) else data
                if not isinstance(content, list):
                    continue
                segments = _parse_supadata_content(content)
                if segments:
                    return VideoTranscript(
                        url=url,
                        title=title,
                        duration=duration,
                        source="supadata_transcript",
                        language=language,
                        segments=segments,
                    )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if last_error:
            raise YouTubeIngestError(
                f"Supadata transcript fallback failed: {last_error}"
            ) from last_error
    return None


async def _fetch_caption_transcript(
    info: dict[str, Any],
    url: str,
) -> VideoTranscript | None:
    selected = _choose_caption_track(info)
    if not selected:
        return None
    track, source_name, language = selected
    body = await _download_caption(track)
    segments = parse_json3(body) if track.get("ext") == "json3" else parse_vtt(body)
    if not segments:
        return None
    return VideoTranscript(
        url=url,
        title=info.get("title") or "YouTube video",
        duration=info.get("duration"),
        source=source_name,
        language=language,
        segments=segments,
    )


def _extract_info(url: str) -> dict[str, Any]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise YouTubeIngestError(
            "yt-dlp не установлен. "
            "Установите зависимости из requirements-bot.txt."
        ) from exc
    with YoutubeDL(
        {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 30,
        }
    ) as ydl:
        return ydl.extract_info(url, download=False)


def _download_audio(url: str, output_dir: Path) -> Path:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise YouTubeIngestError(
            "yt-dlp не установлен. "
            "Установите зависимости из requirements-bot.txt."
        ) from exc
    output_template = str(output_dir / "%(id)s.%(ext)s")
    with YoutubeDL(
        {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 30,
        }
    ) as ydl:
        result = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(result)
    path = Path(filename)
    if not path.exists():
        candidates = list(output_dir.glob("*"))
        if not candidates:
            raise YouTubeIngestError("Не удалось скачать аудио YouTube-видео.")
        return candidates[0]
    return path


def _split_audio(audio_path: Path, output_dir: Path) -> list[Path]:
    chunk_seconds = max(60, get_settings().youtube_audio_chunk_seconds)
    chunk_pattern = output_dir / "chunk_%03d.ogg"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libopus",
        "-b:a",
        "24k",
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-reset_timestamps",
        "1",
        str(chunk_pattern),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise YouTubeIngestError("ffmpeg не установлен в runtime-окружении.") from exc
    except subprocess.CalledProcessError as exc:
        raise YouTubeIngestError(
            f"ffmpeg не смог нарезать аудио: {exc.stderr}"
        ) from exc
    chunks = sorted(output_dir.glob("chunk_*.ogg"))
    if not chunks:
        raise YouTubeIngestError("После нарезки аудио не появилось chunks.")
    return chunks


async def _audio_transcript(url: str, info: dict[str, Any]) -> VideoTranscript:
    with tempfile.TemporaryDirectory(prefix="persona_youtube_") as tmp:
        tmp_path = Path(tmp)
        audio_path = await asyncio.to_thread(_download_audio, url, tmp_path)
        chunk_dir = tmp_path / "chunks"
        chunk_dir.mkdir()
        chunks = await asyncio.to_thread(_split_audio, audio_path, chunk_dir)
        segments: list[TranscriptSegment] = []
        chunk_seconds = max(60, get_settings().youtube_audio_chunk_seconds)
        for index, chunk in enumerate(chunks):
            text = await transcribe_audio(
                chunk.read_bytes(),
                filename=chunk.name,
                content_type="audio/ogg",
                timeout_seconds=180.0,
                language="ru",
            )
            cleaned = _clean_caption_text(text)
            if cleaned:
                start = float(index * chunk_seconds)
                segments.append(
                    TranscriptSegment(
                        start=start,
                        end=start + chunk_seconds,
                        text=cleaned,
                    )
                )
    if not segments:
        raise YouTubeIngestError("Whisper вернул пустую транскрипцию.")
    return VideoTranscript(
        url=url,
        title=info.get("title") or "YouTube video",
        duration=info.get("duration"),
        source="groq_whisper_audio",
        language="ru",
        segments=segments,
    )


async def fetch_transcript(url: str) -> VideoTranscript:
    if not is_youtube_url(url):
        raise YouTubeIngestError("Это не похоже на ссылку YouTube.")
    info: dict[str, Any] | None = None
    try:
        info = await asyncio.to_thread(_extract_info, url)
    except Exception as exc:  # noqa: BLE001
        supadata_transcript = await _fetch_supadata_transcript(url)
        if supadata_transcript:
            return supadata_transcript
        raise YouTubeIngestError(
            "YouTube заблокировал extraction из Railway container. "
            "Настройте SUPADATA_API_KEY в Railway variables для production fallback."
        ) from exc
    try:
        caption_transcript = await _fetch_caption_transcript(info, url)
    except Exception as caption_exc:  # noqa: BLE001
        caption_transcript = None
        supadata_error: Exception | None = None
        try:
            supadata_transcript = await _fetch_supadata_transcript(url)
        except Exception as exc:  # noqa: BLE001
            supadata_transcript = None
            supadata_error = exc
        if supadata_transcript:
            return supadata_transcript
        try:
            return await _audio_transcript(url, info)
        except Exception as audio_exc:  # noqa: BLE001
            supadata_note = f"; supadata: {supadata_error}" if supadata_error else ""
            raise YouTubeIngestError(
                "Не удалось обработать YouTube captions, Supadata fallback "
                "и audio transcription. "
                f"captions: {caption_exc}{supadata_note}; audio: {audio_exc}"
            ) from audio_exc
    if caption_transcript:
        return caption_transcript
    supadata_transcript = await _fetch_supadata_transcript(url)
    if supadata_transcript:
        return supadata_transcript
    return await _audio_transcript(url, info)


def _chunk_text(text: str, max_chars: int = 12_000) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > max_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


async def _extract_useful_points(
    chunk: str,
    *,
    title: str,
    user_takeaway: str,
) -> str:
    prompt = f"""Видео: {title}

Комментарий автора о том, что ему понравилось/было полезно:
<author_takeaway>
{user_takeaway or "Автор пока не добавил личный комментарий."}
</author_takeaway>

Фрагмент транскрипта с таймкодами:
<transcript_chunk>
{chunk}
</transcript_chunk>

Задача:
Извлеки только полезные, интересные, новые или практически применимые мысли из фрагмента.
Учитывай комментарий автора как фильтр важности.
Пропускай рекламу, спонсорские блоки, вступления, самоповторы, пустую мотивацию и воду.
Не выдумывай факты и не добавляй знания вне транскрипта.
Каждый пункт должен иметь таймкод и короткую опору на сказанное в видео.
Верни компактный список на русском.
Если во фрагменте нет полезного содержания, верни: НЕТ ПОЛЕЗНЫХ ПУНКТОВ.
"""
    return await generate_text(
        system_prompt=(
            "Ты аккуратный аналитик YouTube-видео. Работаешь только по транскрипту, "
            "не добавляешь факты извне, отделяешь полезное от рекламы и воды."
        ),
        user_prompt=prompt,
        temperature=0.1,
    )


async def summarize_transcript(
    transcript: VideoTranscript,
    *,
    user_takeaway: str = "",
) -> TranscriptSummary:
    plain = transcript.plain_text
    chunks = _chunk_text(plain)
    partials = []
    for chunk in chunks:
        extracted = await _extract_useful_points(
            chunk,
            title=transcript.title,
            user_takeaway=user_takeaway,
        )
        if "НЕТ ПОЛЕЗНЫХ ПУНКТОВ" not in extracted.upper():
            partials.append(extracted.strip())
    if not partials:
        raise YouTubeIngestError("В транскрипте не найдено полезных пунктов.")
    merge_prompt = f"""Видео: {transcript.title}
URL: {transcript.url}
Источник транскрипта: {transcript.source}

Комментарий автора:
<author_takeaway>
{user_takeaway or "Автор пока не добавил личный комментарий."}
</author_takeaway>

Извлечённые пункты по частям:
<partial_points>
{chr(10).join(partials)}
</partial_points>

Собери итоговый briefing для двойника:
- 5-12 главных идей из видео, без рекламы и воды.
- Для каждой идеи оставь таймкод или диапазон, если он есть.
- Отдельно отметь, что совпало с интересом автора.
- Не добавляй факты, которых нет в пунктах выше.
Верни только briefing на русском.
"""
    briefing = await generate_text(
        system_prompt=(
            "Ты собираешь grounded briefing по YouTube-видео. "
            "Нельзя придумывать факты; можно только сжимать и объединять."
        ),
        user_prompt=merge_prompt,
        temperature=0.1,
    )
    total_chunks = len(chunks)
    processed_chunks = total_chunks
    coverage = round(processed_chunks / total_chunks, 4) if total_chunks else 0.0
    return TranscriptSummary(
        briefing=briefing,
        processed_chunks=processed_chunks,
        total_chunks=total_chunks,
        coverage=coverage,
    )


def compose_editor_input(
    briefing: YouTubeBriefing,
    *,
    presentation_mode: str = "source_reference",
) -> str:
    duration = (
        format_timestamp(briefing.duration or 0) if briefing.duration else "unknown"
    )
    if presentation_mode == "personal":
        task = """Режим подачи: от моего имени.
Пиши как мою личную рефлексию после услышанного: что я понял, с чем соотнёс, какой вывод сделал.
Не упоминай видео, YouTube, автора видео, источник, таймкоды или ссылку.
Не присваивай мне речь, цитаты, биографию, достижения, опыт или позицию спикера.
Не пиши так, будто я сам произнёс эту речь или являюсь её автором.
Если в видео есть сильная формулировка спикера, превращай её в моё осмысление, а не в прямую цитату от моего лица.
Факты и примеры из видео используй только как материал для развития моего вывода, без выдумывания.
Не сохраняй факты из видео как сведения обо мне без моего явного подтверждения."""
    else:
        presentation_mode = "source_reference"
        task = f"""Режим подачи: через видео.
Пиши от моего лица, но естественно упоминай видео как источник идеи: "автор в видео", "в этом видео", "там хорошо разобрали".
Развивай мой личный вывод и подкрепляй его фактами/примерами из видео.
В самом конце готового текста обязательно вставь ссылку отдельной строкой: {briefing.url}
Не сохраняй факты из видео как сведения обо мне без моего явного подтверждения."""

    return f"""<!--source:youtube-->
<youtube_post_mode>{presentation_mode}</youtube_post_mode>
ИСТОЧНИК YOUTUBE:
Название: {briefing.title}
URL: {briefing.url}
Длительность: {duration}
Транскрипт: {briefing.transcript_source}, язык: {briefing.language or "unknown"}
Покрытие транскрипта: {briefing.processed_chunks}/{briefing.total_chunks} chunks ({briefing.coverage})

<author_takeaway>
{briefing.user_takeaway or "Я отправил ссылку без отдельного комментария."}
</author_takeaway>

<video_grounded_notes>
{briefing.briefing}
</video_grounded_notes>

ЗАДАЧА ДЛЯ ДВОЙНИКА:
Используй мой комментарий как главный источник позиции автора.
{task}
"""


async def _build_youtube_editor_result_inner(
    url: str,
    *,
    user_takeaway: str = "",
) -> YouTubeEditorResult:
    transcript = await fetch_transcript(url)
    summary = await summarize_transcript(
        transcript,
        user_takeaway=user_takeaway.strip(),
    )
    briefing = YouTubeBriefing(
        url=url,
        title=transcript.title,
        duration=transcript.duration,
        transcript_source=transcript.source,
        language=transcript.language,
        briefing=summary.briefing,
        user_takeaway=user_takeaway.strip(),
        processed_chunks=summary.processed_chunks,
        total_chunks=summary.total_chunks,
        coverage=summary.coverage,
    )
    source_input = compose_editor_input(
        briefing,
        presentation_mode="source_reference",
    )
    personal_input = compose_editor_input(
        briefing,
        presentation_mode="personal",
    )
    return YouTubeEditorResult(
        editor_input=source_input,
        mode_inputs={
            "source_reference": source_input,
            "personal": personal_input,
        },
        url=url,
        title=transcript.title,
        duration=transcript.duration,
        transcript_source=transcript.source,
        language=transcript.language,
        processed_chunks=summary.processed_chunks,
        total_chunks=summary.total_chunks,
        coverage=summary.coverage,
    )


async def build_youtube_editor_result(
    url: str,
    *,
    user_takeaway: str = "",
) -> YouTubeEditorResult:
    timeout = max(60, get_settings().youtube_ingest_timeout_seconds)
    try:
        return await asyncio.wait_for(
            _build_youtube_editor_result_inner(url, user_takeaway=user_takeaway),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        raise YouTubeIngestError(
            f"YouTube ingest превысил лимит {timeout} секунд."
        ) from exc


async def build_youtube_editor_input(url: str, *, user_takeaway: str = "") -> str:
    result = await build_youtube_editor_result(url, user_takeaway=user_takeaway)
    return result.editor_input
