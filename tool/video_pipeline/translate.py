from __future__ import annotations

import json
import os
import time
from urllib import error, request

from .subtitle import SubtitleBlock


class TranslationError(RuntimeError):
    pass


def _translate_blocks(
    translator,
    blocks: list[SubtitleBlock],
    source_lang: str,
    target_lang: str,
    batch_size: int,
) -> list[SubtitleBlock]:
    translated: list[SubtitleBlock] = []
    for start in range(0, len(blocks), batch_size):
        batch = blocks[start : start + batch_size]
        texts = [block.text for block in batch]
        translated_texts = translator._translate_texts(texts, source_lang, target_lang)
        if len(translated_texts) != len(batch):
            raise TranslationError("Translator returned a different number of subtitles.")
        translated.extend(
            SubtitleBlock(
                index=block.index,
                start=block.start,
                end=block.end,
                text=text.strip(),
            )
            for block, text in zip(batch, translated_texts)
        )
    return translated


def _sort_key(k: str) -> tuple:
    try:
        return (0, int(k))
    except ValueError:
        return (1, k)


class CerebrasTranslator:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("CEREBRAS_API_KEY")
        self.model = model or os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b")
        self.base_url = (base_url or os.environ.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")).rstrip("/")
        self.batch_size = int(os.environ.get("CEREBRAS_TRANSLATE_BATCH_SIZE", "120"))

    def translate_blocks(
        self,
        blocks: list[SubtitleBlock],
        source_lang: str = "auto",
        target_lang: str = "vi",
        batch_size: int | None = None,
    ) -> list[SubtitleBlock]:
        if not self.api_key:
            raise TranslationError("Missing CEREBRAS_API_KEY environment variable.")

        batch_size = batch_size or self.batch_size
        return _translate_blocks(self, blocks, source_lang, target_lang, batch_size)

    def _translate_texts(self, texts: list[str], source_lang: str, target_lang: str) -> list[str]:
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You translate subtitles. Return ONLY a JSON array of translated strings. "
                        "No thinking, no explanation, no markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_language": source_lang,
                            "target_language": target_lang,
                            "subtitles": texts,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }

        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "supertonic-video-localizer/0.1",
            },
            method="POST",
        )

        data = self._send_with_retry(req)

        try:
            content = data["choices"][0]["message"]["content"].strip()
            return self._parse_json_array(content)
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError(f"Unexpected Cerebras response: {data}") from exc

    def _send_with_retry(self, req: request.Request) -> dict:
        last_detail = ""
        for attempt in range(4):
            try:
                with request.urlopen(req, timeout=180) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_detail = f"Cerebras API error {exc.code}: {detail}"
                if exc.code != 429 or attempt == 3:
                    raise TranslationError(last_detail) from exc
                retry_after = exc.headers.get("Retry-After")
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else 20 * (attempt + 1)
                time.sleep(wait_seconds)
            except Exception as exc:
                raise TranslationError(str(exc)) from exc
        raise TranslationError(last_detail or "Cerebras API request failed")

    @staticmethod
    def _parse_json_array(content: str) -> list[str]:
        if content.startswith("```"):
            content = content.strip("`")
            content = content.removeprefix("json").strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            start = content.find("[")
            end = content.rfind("]")
            if start == -1 or end == -1 or end <= start:
                raise TranslationError(f"Translator did not return JSON: {content}") from exc
            parsed = json.loads(content[start : end + 1])

        if isinstance(parsed, list):
            if all(isinstance(item, str) for item in parsed):
                return parsed
            raise TranslationError(f"Translator returned invalid JSON array: {content}")

        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list) and all(isinstance(item, str) for item in v):
                    return v
            keys = sorted(parsed.keys(), key=_sort_key)
            items = [parsed[k] for k in keys]
            if all(isinstance(item, str) for item in items):
                return items
            raise TranslationError(f"Translator returned invalid JSON dict: {content}")

        raise TranslationError(f"Translator returned unexpected JSON type: {content}")


class GoogleTranslator:
    """Subtitle translator using Google Translate via deep-translator (no API key needed)."""

    def __init__(self, batch_size: int | None = None):
        self.batch_size = batch_size or int(os.environ.get("GOOGLE_TRANSLATE_BATCH_SIZE", "30"))

    def translate_blocks(
        self,
        blocks: list[SubtitleBlock],
        source_lang: str = "auto",
        target_lang: str = "vi",
        batch_size: int | None = None,
    ) -> list[SubtitleBlock]:
        return _translate_blocks(self, blocks, source_lang, target_lang, batch_size or self.batch_size)

    def _translate_texts(self, texts: list[str], source_lang: str, target_lang: str) -> list[str]:
        try:
            from deep_translator import GoogleTranslator as _GT
        except ImportError:
            raise TranslationError(
                "Missing deep-translator. Install it with: uv add deep-translator"
            )

        src = source_lang if source_lang != "auto" else "auto"
        gt = _GT(source=src, target=target_lang)
        try:
            result = gt.translate_batch(texts)
        except Exception as exc:
            raise TranslationError(f"Google Translate error: {exc}") from exc
        return [t or "" for t in result]


class OllamaTranslator:
    """Local subtitle translator backed by Ollama's JSON chat API."""

    def __init__(self, model: str | None = None, base_url: str | None = None):
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen3:4b")
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.batch_size = int(os.environ.get("OLLAMA_TRANSLATE_BATCH_SIZE", "2"))

    def translate_blocks(
        self,
        blocks: list[SubtitleBlock],
        source_lang: str = "auto",
        target_lang: str = "vi",
        batch_size: int | None = None,
    ) -> list[SubtitleBlock]:
        return _translate_blocks(self, blocks, source_lang, target_lang, batch_size or self.batch_size)

    def _translate_texts(self, texts: list[str], source_lang: str, target_lang: str) -> list[str]:
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 4096},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You translate subtitles. Return ONLY a JSON array of translated strings. "
                        "No thinking, no explanation, no markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Translate from {source_lang} to {target_lang}. Return ONLY a JSON array.\n"
                    + "\n---\n".join(f"{i+1}. {t}" for i, t in enumerate(texts)),
                },
            ],
        }
        req = request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        return self._send_with_retry(req)

    def _send_with_retry(self, req: request.Request) -> list[str]:
        last_detail = ""
        for attempt in range(4):
            try:
                with request.urlopen(req, timeout=300) as response:
                    data = json.loads(response.read().decode("utf-8"))
                content = data["message"]["content"].strip()
                return CerebrasTranslator._parse_json_array(content)
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_detail = f"Ollama API error {exc.code}: {detail}"
                if exc.code != 429 or attempt == 3:
                    raise TranslationError(last_detail) from exc
                retry_after = exc.headers.get("Retry-After")
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else 10 * (attempt + 1)
                time.sleep(wait_seconds)
            except error.URLError as exc:
                if attempt == 3:
                    raise TranslationError(
                        f"Could not reach Ollama at {self.base_url}. Start it with `ollama serve`."
                    ) from exc
                time.sleep(5 * (attempt + 1))
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                last_detail = f"Unexpected Ollama response: {exc}"
                if attempt == 3:
                    raise TranslationError(last_detail) from exc
                time.sleep(3)
        raise TranslationError(last_detail or "Ollama request failed")
