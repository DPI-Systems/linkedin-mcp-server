"""Markdown -> the blocks a LinkedIn article editor is filled with.

The editor takes rich text (pasted as HTML) and images (uploaded one by one),
so an article becomes an ordered list of ``html`` and ``image`` blocks. Only
the formatting the editor offers survives; anything else is reported in
``warnings`` instead of disappearing silently.
"""

import html
import pathlib
import re
from dataclasses import dataclass, field

from markdown_it import MarkdownIt
from markdown_it.token import Token

# Formats LinkedIn's article editor keeps when pasted (strikethrough is not one).
ALLOWED_TAGS = {
    "h1", "h3", "p", "strong", "em", "a", "ul", "ol", "li",
    "blockquote", "pre", "code", "hr", "br",
}  # fmt: skip
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_RAW_HTML = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(\s[^>]*)?/?>")
_FENCED_CODE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_TABLE_ROW = re.compile(r"^\s*\|?.*\|.*\n\s*\|?\s*:?-{3,}", re.MULTILINE)


@dataclass(frozen=True)
class HtmlBlock:
    html: str
    kind: str = "html"


@dataclass(frozen=True)
class ImageBlock:
    path: pathlib.Path
    alt: str
    kind: str = "image"


@dataclass
class ArticlePlan:
    blocks: list[HtmlBlock | ImageBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def images(self) -> list[ImageBlock]:
        return [b for b in self.blocks if isinstance(b, ImageBlock)]


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.enable("strikethrough")
    return md


def _heading_level(tag: str) -> str:
    # The editor has two heading sizes; pasted, an <h2> lands as the small
    # one, so the large one is sent as <h1>.
    return "h1" if tag in ("h1", "h2") else "h3"


def _resolve_image(src: str, base_dir: pathlib.Path) -> pathlib.Path:
    if re.match(r"^[a-z]+://", src, re.IGNORECASE):
        raise ValueError(
            f"Image {src!r} is a URL; give a local file path (downloading "
            "images is left to the caller)."
        )
    path = (base_dir / src).expanduser().resolve()
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f"Image {src!r} is not one of {sorted(IMAGE_SUFFIXES)}")
    if not path.is_file():
        raise ValueError(f"Image {src!r} does not exist ({path})")
    return path


def plan_article(markdown: str, base_dir: pathlib.Path | str = ".") -> ArticlePlan:
    """Split ``markdown`` into editor blocks; raises ValueError on bad images."""
    base = pathlib.Path(base_dir).expanduser()
    plan = ArticlePlan()
    if _TABLE_ROW.search(markdown):
        plan.warnings.append(
            "Tables are not supported by LinkedIn articles; they are kept as "
            "plain text. Use a list or an image instead."
        )
    if _RAW_HTML.search(_FENCED_CODE.sub("", markdown)):
        plan.warnings.append(
            "Raw HTML is not interpreted; it will appear as plain text."
        )
    md = _parser()
    tokens = md.parse(markdown)
    pending: list[Token] = []

    def flush() -> None:
        if pending:
            rendered = md.renderer.render(pending, md.options, {}).strip()
            if rendered:
                plan.blocks.append(HtmlBlock(rendered))
            pending.clear()

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "heading_open":
            tok.tag = _heading_level(tok.tag)
            tokens[i + 2].tag = tok.tag
        if tok.type == "paragraph_open" and tokens[i + 1].type == "inline":
            inline = tokens[i + 1]
            images = [c for c in inline.children or [] if c.type == "image"]
            if images:
                # Text and images in one paragraph: images move out after
                # the text, in order, since the editor places them as blocks.
                text_children = [c for c in inline.children if c.type != "image"]
                has_text = any(
                    c.type == "text" and c.content.strip() for c in text_children
                ) or any(c.type not in ("text", "softbreak") for c in text_children)
                if has_text:
                    inline.children = text_children
                    pending.extend(tokens[i : i + 3])
                    plan.warnings.append(
                        "An image inside a paragraph was moved below that paragraph."
                    )
                flush()
                for img in images:
                    path = _resolve_image(img.attrGet("src") or "", base)
                    alt = "".join(c.content for c in img.children or [])
                    plan.blocks.append(ImageBlock(path, alt or path.stem))
                i += 3
                continue
        pending.append(tok)
        i += 1
    flush()

    for block in plan.blocks:
        if isinstance(block, HtmlBlock):
            unknown = set(re.findall(r"</?([a-z0-9]+)", block.html)) - ALLOWED_TAGS
            if unknown:
                plan.warnings.append(
                    f"Formatting not offered by the editor will be lost: {sorted(unknown)}"
                )
    if not plan.blocks:
        raise ValueError("The article body is empty.")
    return plan


def plain_text(plan: ArticlePlan) -> str:
    """The text the editor should end up holding, to verify what was typed."""
    out = []
    for block in plan.blocks:
        if isinstance(block, HtmlBlock):
            text = re.sub(r"<[^>]+>", " ", block.html)
            out.append(html.unescape(" ".join(text.split())))
    return " ".join(out)
