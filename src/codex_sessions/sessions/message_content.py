import json
from typing import Any


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False, indent=2)

    has_image_item = any(is_image_content_item(item) for item in content)
    parts = []
    for item in content:
        if isinstance(item, dict):
            if isinstance(item.get("text"), str):
                text = item["text"]
                if has_image_item and is_image_wrapper_text(text):
                    continue
                parts.append(text)
            elif is_image_content_item(item):
                parts.append(f"[input image: {item.get('image_url', '')}]")
            elif item.get("type") == "local_image":
                parts.append(f"[local image: {item.get('path') or item.get('name') or ''}]")
            else:
                parts.append(json.dumps(item, ensure_ascii=False, indent=2))
        else:
            parts.append(str(item))
    return "\n\n".join(part for part in parts if part)


def is_image_content_item(item: Any) -> bool:
    return isinstance(item, dict) and (
        item.get("type") in {"image_url", "input_image"} or "image_url" in item
    )


def is_image_wrapper_text(text: str) -> bool:
    return text.strip().lower() in {"<image>", "</image>"}


def is_injected_user_context(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(("# AGENTS.md instructions", "<environment_context>"))


def searchable_user_message_text(text: str) -> str:
    if is_injected_user_context(text):
        return ""

    stripped = text.lstrip()
    if stripped.startswith("# Context from my IDE setup:"):
        # IDE context is useful metadata, but default search should target the user's request.
        marker = "## My request for Codex:"
        marker_index = text.find(marker)
        if marker_index == -1:
            return ""
        return text[marker_index + len(marker) :].strip()

    return text
