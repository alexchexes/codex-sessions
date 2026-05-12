from typing import Any

from codex_sessions.formats.markdown.formatting import render_json_block_content
from codex_sessions.formats.markdown.images import (
    MarkdownImageHandler,
    is_image_content_item,
    is_image_wrapper_text,
)


def content_to_text(content: Any, image_handler: MarkdownImageHandler | None = None) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        value = image_handler.transform_value(content) if image_handler else content
        return render_json_block_content(value)

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
                if image_handler:
                    parts.append(image_handler.render_image(item.get("image_url"), "input image"))
                else:
                    parts.append(f"[input image: {item.get('image_url', '')}]")
            elif item.get("type") == "local_image":
                parts.append(f"[local image: {item.get('path') or item.get('name') or ''}]")
            else:
                value = image_handler.transform_value(item) if image_handler else item
                parts.append(render_json_block_content(value))
        else:
            parts.append(str(item))
    return "\n\n".join(part for part in parts if part)


def is_injected_user_context(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(("# AGENTS.md instructions", "<environment_context>"))


def searchable_user_message_text(text: str) -> str:
    if is_injected_user_context(text):
        return ""

    stripped = text.lstrip()
    if stripped.startswith("# Context from my IDE setup:"):
        marker = "## My request for Codex:"
        marker_index = text.find(marker)
        if marker_index == -1:
            return ""
        return text[marker_index + len(marker) :].strip()

    return text
