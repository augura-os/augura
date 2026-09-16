"""Vision analysis + embeddings over any OpenAI-compatible provider.

Kimi/Moonshot (api.moonshot.cn) does not support OpenAI's strict
``json_schema`` response format nor custom ``temperature`` on k2.5+ models,
so for those endpoints we use JSON Mode (``json_object``) and default
sampling parameters. If strict mode is rejected by any other provider we
fall back to JSON Mode and validate with Pydantic either way — output stays
strictly structured (contract §4).
"""

from __future__ import annotations

import base64
import json
from typing import Literal, Sequence

from openai import BadRequestError, OpenAI

from app.schemas.analysis import AnalysisPayload
from app.services import ip_pack
from app.services.settings import AIConfig

# Strict JSON schema for response_format — mirrors contract §4 exactly.
ANALYSIS_JSON_SCHEMA: dict[str, object] = {
    "name": "creative_analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "hook": {"type": "string"},
            "conflict": {"type": "string"},
            "gameplay": {"type": "string"},
            "reward": {"type": "string"},
            "characters": {"type": "array", "items": {"type": "string"}},
            "environment": {"type": "array", "items": {"type": "string"}},
            "emotion": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "variant_factors": {"type": "array", "items": {"type": "string"}},
            "creative_name": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": [
            "summary",
            "hook",
            "conflict",
            "gameplay",
            "reward",
            "characters",
            "environment",
            "emotion",
            "tags",
            "variant_factors",
            "creative_name",
            "confidence",
        ],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = ip_pack.ANALYSIS_SYSTEM_PROMPT

_USER_PROMPT_VIDEO = ip_pack.ANALYSIS_USER_PROMPT_VIDEO
_USER_PROMPT_IMAGE = ip_pack.ANALYSIS_USER_PROMPT_IMAGE


class AnalysisService:
    def __init__(self, config: AIConfig) -> None:
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self._vision_model = config.vision_model
        self._embedding_model = config.embedding_model
        self._json_mode_only = config.is_moonshot

    def analyze_frames(
        self,
        frames: Sequence[tuple[bytes, str]],
        media_type: Literal["video", "image"],
    ) -> AnalysisPayload:
        """Send frames to the vision model under the strict §4 schema.

        ``frames`` items are ``(data, mime_type)`` pairs.
        """
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    _USER_PROMPT_VIDEO if media_type == "video" else _USER_PROMPT_IMAGE
                ),
            }
        ]
        for data, mime_type in frames:
            encoded = base64.b64encode(data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )
        messages: list[dict[str, object]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        raw = self._complete(messages)
        parsed: object = json.loads(raw)
        return AnalysisPayload.model_validate(_coerce_list_fields(parsed))

    def _complete(self, messages: list[dict[str, object]]) -> str:
        """Strict json_schema when supported; JSON Mode otherwise (+retry)."""
        if not self._json_mode_only:
            try:
                completion = self._client.chat.completions.create(
                    model=self._vision_model,
                    messages=messages,  # type: ignore[arg-type]
                    response_format={  # type: ignore[typeddict-item]
                        "type": "json_schema",
                        "json_schema": ANALYSIS_JSON_SCHEMA,
                    },
                    temperature=0.2,
                    max_tokens=2000,
                )
                return completion.choices[0].message.content or "{}"
            except BadRequestError:
                pass  # provider rejected strict mode → JSON Mode below
        completion = self._client.chat.completions.create(
            model=self._vision_model,
            messages=messages,  # type: ignore[arg-type]
            response_format={"type": "json_object"},
        )
        return completion.choices[0].message.content or "{}"

    def embed(self, text: str) -> list[float]:
        """Embedding used for cosine clustering (§5.1).

        Raises when no embedding model is configured or the provider does
        not offer an embeddings endpoint (e.g. Kimi) — callers fall back to
        local text similarity clustering.
        """
        if not self._embedding_model:
            raise RuntimeError("未配置 embedding 模型")
        response = self._client.embeddings.create(
            model=self._embedding_model, input=text.strip() or " "
        )
        return [float(value) for value in response.data[0].embedding]


_LIST_FIELDS = ("characters", "environment", "emotion", "tags", "variant_factors")


def _coerce_list_fields(parsed: object) -> object:
    """Normalize list fields that arrive as plain strings.

    JSON Mode (Kimi/Moonshot) does not enforce array shapes the way strict
    json_schema does; models sometimes answer "a, b, c" instead of a real
    array. Split on comma-ish separators so validation stays strict.

    Models also occasionally interleave numbers into string arrays
    (score/coordinate hallucinations like ["张三", -3.5, "李四", 0.75]) —
    drop non-string items instead of failing the whole analysis.
    """
    if not isinstance(parsed, dict):
        return parsed
    result = dict(parsed)
    for field in _LIST_FIELDS:
        value = result.get(field)
        if isinstance(value, str):
            items = [
                item.strip()
                for item in value.replace("，", ",").replace("、", ",").split(",")
            ]
            result[field] = [item for item in items if item]
        elif isinstance(value, list):
            result[field] = [item for item in value if isinstance(item, str)]
    return result
