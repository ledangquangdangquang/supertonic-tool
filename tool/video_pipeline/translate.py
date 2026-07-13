from __future__ import annotations

import json
import os
import time
from urllib import error, request

from .subtitle import SubtitleBlock


class TranslationError(RuntimeError):
    pass


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
        translated: list[SubtitleBlock] = []
        for start in range(0, len(blocks), batch_size):
            batch = blocks[start : start + batch_size]
            texts = [block.text for block in batch]
            translated_texts = self._translate_texts(texts, source_lang, target_lang)
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

    def _translate_texts(self, texts: list[str], source_lang: str, target_lang: str) -> list[str]:
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You translate subtitles. Return only a JSON array of strings. "
                        "Keep the same array length and order. Do not include timestamps, numbering, or explanations."
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
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise TranslationError(f"Translator returned invalid JSON array: {content}")
        return parsed
