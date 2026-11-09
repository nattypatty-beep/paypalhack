"""Deliverable inspection.

The verifier is only as good as its evidence.  This module reads the actual
file bytes and extracts objective facts: format, dimensions, palette size,
whether there is a full-bleed background, placeholder copy, word counts, and a
content hash.  The LLM layer can add a qualitative opinion, but it cannot argue
with a 400×200 PNG that claims to be a 512×512 master.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

NAMED_COLORS = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "orange": "#ffa500",
    "purple": "#800080",
    "pink": "#ffc0cb",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "maroon": "#800000",
    "navy": "#000080",
    "teal": "#008080",
    "olive": "#808000",
    "lime": "#00ff00",
    "aqua": "#00ffff",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "fuchsia": "#ff00ff",
    "brown": "#a52a2a",
    "beige": "#f5f5dc",
    "ivory": "#fffff0",
    "gold": "#ffd700",
    "goldenrod": "#daa520",
    "coral": "#ff7f50",
    "salmon": "#fa8072",
    "tan": "#d2b48c",
}

PLACEHOLDER_TERMS = ["lorem ipsum", "placeholder", "dummy text", "todo", "tbd", "xxx", "sample text", "insert text"]

TEXT_EXTENSIONS = {"md", "txt", "csv", "json", "html", "css", "js", "py", "ts", "tsx", "jsx"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
AUDIO_EXTENSIONS = {"mp3", "wav", "flac", "m4a", "ogg"}
VIDEO_EXTENSIONS = {"mp4", "mov", "webm", "avi", "mkv"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_length(value: str | None, reference: float | None = None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    percent = text.endswith("%")
    text = re.sub(r"(px|pt|mm|cm|in|em|rem)$", "", text)
    try:
        number = float(text.strip())
    except ValueError:
        return None
    if percent:
        if reference is None:
            return None
        return reference * number / 100.0
    return number


def _normalize_color(value: str | None) -> str | None:
    if not value:
        return None
    color = value.strip().lower()
    if color in {"none", "transparent", "currentcolor", "inherit", "initial", "unset", ""}:
        return None
    if color.startswith("url("):
        return None
    if color.startswith("#"):
        hexpart = color[1:]
        if len(hexpart) in {3, 4}:
            hexpart = "".join(char * 2 for char in hexpart[:3])
        if len(hexpart) == 8:
            hexpart = hexpart[:6]
        if len(hexpart) == 6 and re.fullmatch(r"[0-9a-f]{6}", hexpart):
            return f"#{hexpart}"
        return color
    rgb = re.match(r"rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)", color)
    if rgb:
        r, g, b = (max(0, min(255, int(float(part)))) for part in rgb.groups())
        return f"#{r:02x}{g:02x}{b:02x}"
    if color in NAMED_COLORS:
        return NAMED_COLORS[color]
    return color


def _style_colors(style: str | None) -> set[str]:
    colors: set[str] = set()
    if not style:
        return colors
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        prop, _, value = declaration.partition(":")
        if prop.strip().lower() in {"fill", "stroke", "stop-color", "color", "flood-color"}:
            normalized = _normalize_color(value)
            if normalized:
                colors.add(normalized)
    return colors


def _svg_info(data: bytes) -> dict[str, Any]:
    info: dict[str, Any] = {
        "format": "svg",
        "valid": False,
        "width": None,
        "height": None,
        "colors": [],
        "color_count": 0,
        "text": "",
        "word_count": 0,
        "elements": 0,
        "has_full_bleed_background": False,
        "has_script": False,
        "error": None,
    }
    lowered = data[:20000].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        info["error"] = "SVG contains a DTD/entity declaration and was rejected"
        return info
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        info["error"] = f"invalid SVG: {exc}"
        return info

    info["valid"] = True
    viewbox: tuple[float, float, float, float] | None = None
    for key, value in root.attrib.items():
        if _local(key) == "viewbox":
            parts = re.split(r"[\s,]+", value.strip())
            if len(parts) == 4:
                try:
                    viewbox = tuple(float(p) for p in parts)  # type: ignore[assignment]
                except ValueError:
                    viewbox = None
            break

    width = _parse_length(root.attrib.get("width"), viewbox[2] if viewbox else None)
    height = _parse_length(root.attrib.get("height"), viewbox[3] if viewbox else None)
    if viewbox:
        width = width or viewbox[2]
        height = height or viewbox[3]
    info["width"] = int(round(width)) if width else None
    info["height"] = int(round(height)) if height else None

    colors: set[str] = set()
    texts: list[str] = []
    elements = 0
    has_image = False
    full_bleed = False
    has_script = False

    vb_x, vb_y, vb_w, vb_h = viewbox if viewbox else (0.0, 0.0, width or 0.0, height or 0.0)

    for elem in root.iter():
        tag = _local(elem.tag)
        elements += 1
        if tag == "script":
            has_script = True
        if tag == "image":
            has_image = True
        attributes = { _local(k): v for k, v in elem.attrib.items() }
        for prop in ("fill", "stroke", "stop-color", "color", "flood-color"):
            if prop in attributes:
                normalized = _normalize_color(attributes[prop])
                if normalized:
                    colors.add(normalized)
        colors.update(_style_colors(attributes.get("style")))

        if tag in {"text", "tspan", "title", "desc"}:
            content = "".join(elem.itertext()).strip()
            if content:
                texts.append(content)

        if tag == "rect":
            fill = _normalize_color(attributes.get("fill"))
            style_fill = None
            if attributes.get("style"):
                match = re.search(r"fill\s*:\s*([^;]+)", attributes["style"], re.I)
                if match:
                    style_fill = _normalize_color(match.group(1))
            effective_fill = style_fill or fill
            if effective_fill:
                x = _parse_length(attributes.get("x"), vb_w) or 0.0
                y = _parse_length(attributes.get("y"), vb_h) or 0.0
                w = _parse_length(attributes.get("width"), vb_w)
                h = _parse_length(attributes.get("height"), vb_h)
                if (
                    w is not None
                    and h is not None
                    and vb_w > 0
                    and vb_h > 0
                    and w >= vb_w * 0.95
                    and h >= vb_h * 0.95
                    and x <= vb_x + vb_w * 0.05
                    and y <= vb_y + vb_h * 0.05
                ):
                    full_bleed = True

    unique_text: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if text not in seen:
            seen.add(text)
            unique_text.append(text)
    text_content = " ".join(unique_text)
    info.update(
        {
            "colors": sorted(colors),
            "color_count": len(colors),
            "text": text_content[:4000],
            "word_count": len(re.findall(r"[A-Za-z0-9']+", text_content)),
            "elements": elements,
            "has_full_bleed_background": full_bleed,
            "has_script": has_script,
            "has_raster_image": has_image,
        }
    )
    return info


def _png_info(data: bytes) -> tuple[int | None, int | None]:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    return None, None


def _jpeg_info(data: bytes) -> tuple[int | None, int | None]:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return int(width), int(height)
        index += 2 + length
    return None, None


def _gif_info(data: bytes) -> tuple[int | None, int | None]:
    if len(data) >= 10 and data[:6] in {b"GIF87a", b"GIF89a"}:
        width, height = struct.unpack("<HH", data[6:10])
        return int(width), int(height)
    return None, None


def _webp_info(data: bytes) -> tuple[int | None, int | None]:
    if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return width, height
        if chunk == b"VP8 ":
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
            return width, height
    return None, None


def _pdf_info(data: bytes) -> tuple[int | None, int | None]:
    match = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", data[:500000])
    if match:
        return int(float(match.group(1))), int(float(match.group(2)))
    return None, None


def inspect_bytes(data: bytes, name: str) -> dict[str, Any]:
    extension = Path(name).suffix.lower().lstrip(".")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    info: dict[str, Any] = {
        "name": name,
        "ext": extension,
        "mime": mime,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "valid": True,
        "error": None,
        "format": extension or "unknown",
        "width": None,
        "height": None,
        "colors": [],
        "color_count": 0,
        "text": "",
        "word_count": 0,
        "elements": 0,
        "has_full_bleed_background": False,
        "has_script": False,
    }

    try:
        if extension == "svg":
            info.update(_svg_info(data))
        elif extension == "png":
            info["width"], info["height"] = _png_info(data)
            info["valid"] = info["width"] is not None
        elif extension in {"jpg", "jpeg"}:
            info["width"], info["height"] = _jpeg_info(data)
            info["valid"] = info["width"] is not None
        elif extension == "gif":
            info["width"], info["height"] = _gif_info(data)
            info["valid"] = info["width"] is not None
        elif extension == "webp":
            info["width"], info["height"] = _webp_info(data)
            info["valid"] = info["width"] is not None
        elif extension == "pdf":
            info["width"], info["height"] = _pdf_info(data)
            info["valid"] = data[:5] == b"%PDF-"
        elif extension == "zip":
            info["valid"] = data[:4] == b"PK\x03\x04"
        elif extension in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
            info["valid"] = len(data) > 1024
        elif extension in TEXT_EXTENSIONS or extension in {"doc", "docx"}:
            text = data[:120000].decode("utf-8", errors="ignore")
            info["text"] = text[:4000]
            info["word_count"] = len(re.findall(r"[A-Za-z0-9']+", text))
        elif not extension:
            info["valid"] = False
            info["error"] = "file has no extension"
    except Exception as exc:  # noqa: BLE001 - a malformed file should never crash the verifier
        info["valid"] = False
        info["error"] = f"could not inspect file: {exc}"

    if info["error"]:
        info["valid"] = False

    text_lower = str(info.get("text", "")).lower()
    info["placeholder_terms"] = sorted(
        {term for term in PLACEHOLDER_TERMS if term in text_lower}
    )
    if info.get("has_script"):
        info["valid"] = False
        info["error"] = "SVG contains a script element"
    return info


def inspect_path(path: Path, name: str | None = None) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        display_name = name or path.name
        extension = Path(display_name).suffix.lower().lstrip(".")
        return {
            "name": display_name,
            "ext": extension,
            "mime": mimetypes.guess_type(display_name)[0] or "application/octet-stream",
            "size": 0,
            "sha256": "",
            "valid": False,
            "error": f"could not read file: {exc}",
            "format": extension or "unknown",
            "width": None,
            "height": None,
            "colors": [],
            "color_count": 0,
            "text": "",
            "word_count": 0,
            "elements": 0,
            "has_full_bleed_background": False,
            "has_script": False,
            "placeholder_terms": [],
        }
    return inspect_bytes(data, name or path.name)
