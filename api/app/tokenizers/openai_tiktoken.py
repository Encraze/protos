from __future__ import annotations

from collections.abc import Iterable

import tiktoken


def _encoding_for(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_openai_input_tokens(
    *, model: str, messages: Iterable[dict[str, object]]
) -> int:
    enc = _encoding_for(model)
    total = 0
    for m in messages:
        role = str(m.get("role") or "")
        content = m.get("content")
        total += len(enc.encode(role)) + 4
        if isinstance(content, str):
            total += len(enc.encode(content))
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        total += len(enc.encode(text))
    total += 2
    return total
