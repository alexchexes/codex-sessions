import base64
import binascii
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

DATA_IMAGE_PREFIX_CHARS = 24
DATA_IMAGE_URL_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.*)$", re.DOTALL)
IMAGE_EXTENSION_BY_MIME_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/svg+xml": "svg",
}


@dataclass(frozen=True)
class DataImageUrl:
    media_type: str
    encoded_data: str


class MarkdownImageHandler:
    def __init__(self, mode: str, output_path: Path, input_path: Path) -> None:
        self.mode = mode
        self.output_path = output_path
        self.input_path = input_path
        self.asset_dir = output_path.with_name(f"{output_path.stem}_assets")
        # A single data URL can appear in multiple sanitized views; extract it once.
        self._links_by_url: dict[str, str] = {}
        self.source_line_number: int | None = None

    def set_source_line(self, line_number: int) -> None:
        self.source_line_number = line_number

    def render_image(self, image_url: Any, label: str = "image") -> str:
        if not isinstance(image_url, str):
            return f"[{label}: missing image_url]"

        data_image = parse_data_image_url(image_url)
        if data_image is None:
            return f"[{label}: {image_url}]"

        if self.mode == "inline":
            return "\n".join(
                [
                    self.inline_image_comment(),
                    f"![{label}]({image_url})",
                ]
            )
        if self.mode == "extract":
            link = self.extract_data_image(image_url, data_image)
            if link:
                return f"![{label}]({link})"
            return f"[{label}: invalid {data_image.media_type} data URL]"
        return f"[{label}: {self.describe_truncated_image(data_image)}]"

    def transform_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.transform_value(inner) for key, inner in value.items()}
        if isinstance(value, list):
            return [self.transform_value(item) for item in value]
        if isinstance(value, str):
            data_image = parse_data_image_url(value)
            if data_image is None:
                return value
            if self.mode == "inline":
                return value
            if self.mode == "extract":
                link = self.extract_data_image(value, data_image)
                return link if link else f"[invalid {data_image.media_type} data URL]"
            return (
                f"data:{data_image.media_type};base64,{self.describe_truncated_image(data_image)}"
            )
        return value

    def extract_data_image(self, image_url: str, data_image: DataImageUrl) -> str | None:
        if image_url in self._links_by_url:
            return self._links_by_url[image_url]

        try:
            image_bytes = base64.b64decode("".join(data_image.encoded_data.split()), validate=True)
        except (binascii.Error, ValueError):
            return None

        digest = hashlib.sha256(image_bytes).hexdigest()[:12]
        extension = image_extension(data_image.media_type)
        filename = f"image-{len(self._links_by_url) + 1:03d}-{digest}.{extension}"
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        image_path = self.asset_dir / filename
        if not image_path.exists():
            image_path.write_bytes(image_bytes)

        link = markdown_relative_link(image_path, self.output_path)
        self._links_by_url[image_url] = link
        return link

    def describe_truncated_image(self, data_image: DataImageUrl) -> str:
        return describe_data_image(data_image, self.source_reference())

    def source_reference(self) -> str:
        source = str(self.input_path)
        if self.source_line_number is not None:
            source = f"{source}:{self.source_line_number}"
        return markdown_code_span(source)

    def source_comment_reference(self) -> str:
        source = str(self.input_path)
        if self.source_line_number is not None:
            source = f"{source}:{self.source_line_number}"
        return escape_markdown_reference_comment_text(source)

    def inline_image_comment(self) -> str:
        # Markdown reference comments avoid HTML-comment parser differences around "--".
        return (
            "[//]: # (Inline image; use --md-images truncate or --md-images extract; "
            f"Source: {self.source_comment_reference()}.)"
        )


def parse_data_image_url(value: str) -> DataImageUrl | None:
    match = DATA_IMAGE_URL_RE.match(value)
    if not match:
        return None
    return DataImageUrl(
        media_type=match.group(1).lower(),
        encoded_data=match.group(2),
    )


def describe_data_image(data_image: DataImageUrl, source_reference: str | None = None) -> str:
    compact_data = "".join(data_image.encoded_data.split())
    base64_prefix = compact_data[:DATA_IMAGE_PREFIX_CHARS]
    if len(compact_data) > DATA_IMAGE_PREFIX_CHARS:
        base64_prefix = f"{base64_prefix}..."
    parts = [
        f"{data_image.media_type} data URL",
        f"{len(compact_data)} base64 chars truncated",
    ]
    if source_reference:
        parts.append(f"source {source_reference}")
    parts.append(f"base64 prefix {markdown_code_span(base64_prefix)}")
    return "; ".join(parts)


def image_extension(media_type: str) -> str:
    extension = IMAGE_EXTENSION_BY_MIME_TYPE.get(media_type)
    if extension:
        return extension
    subtype = media_type.split("/", 1)[-1].split("+", 1)[0].lower()
    sanitized = re.sub(r"[^a-z0-9]+", "", subtype)
    return sanitized or "bin"


def markdown_relative_link(target_path: Path, markdown_path: Path) -> str:
    relative_path = os.path.relpath(target_path, start=markdown_path.parent)
    return quote(Path(relative_path).as_posix(), safe="/._-")


def markdown_code_span(text: str) -> str:
    if "`" not in text:
        return f"`{text}`"
    return f"`` {text} ``"


def escape_markdown_reference_comment_text(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").replace(")", r"\)")
