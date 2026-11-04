"""Offline tests for Markdown -> article editor blocks."""

import pytest

from dpi_salesnav.article_markdown import (
    HtmlBlock,
    ImageBlock,
    plain_text,
    plan_article,
)


@pytest.fixture
def images(tmp_path):
    for name in ("grafico.png", "foto.jpg"):
        (tmp_path / name).write_bytes(b"\x89PNG")
    return tmp_path


def test_formatting_maps_to_editor_html(tmp_path):
    md = """# Titular

Texto con **negrita**, *cursiva*, ~~tachado~~ y [enlace](https://licita-ia.es).

### Subapartado

- uno
- dos

1. primero
2. segundo

> una cita

---
"""
    plan = plan_article(md, tmp_path)
    assert plan.warnings == ["Formatting not offered by the editor will be lost: ['s']"]
    assert len(plan.blocks) == 1
    out = plan.blocks[0].html
    assert "<h1>Titular</h1>" in out
    assert "<h3>Subapartado</h3>" in out
    assert "<strong>negrita</strong>" in out
    assert "<em>cursiva</em>" in out
    assert "<s>tachado</s>" in out
    assert '<a href="https://licita-ia.es">enlace</a>' in out
    assert "<ul>\n<li>uno</li>" in out
    assert "<ol>\n<li>primero</li>" in out
    assert "<blockquote>" in out
    assert "<hr />" in out


def test_images_split_the_body_in_order(images):
    md = "Intro.\n\n![Un gráfico](grafico.png)\n\nMedio.\n\n![](foto.jpg)\n\nFin."
    plan = plan_article(md, images)
    kinds = [b.kind for b in plan.blocks]
    assert kinds == ["html", "image", "html", "image", "html"]
    first, second = plan.images
    assert first.path == (images / "grafico.png").resolve()
    assert first.alt == "Un gráfico"
    assert second.alt == "foto"
    assert plain_text(plan) == "Intro. Medio. Fin."


def test_image_inside_text_moves_below_with_warning(images):
    plan = plan_article("Mira esto ![g](grafico.png) aquí.", images)
    assert isinstance(plan.blocks[0], HtmlBlock)
    assert "Mira esto" in plan.blocks[0].html
    assert isinstance(plan.blocks[1], ImageBlock)
    assert any("moved below" in w for w in plan.warnings)


@pytest.mark.parametrize(
    "src, message",
    [
        ("https://example.com/a.png", "URL"),
        ("falta.png", "does not exist"),
        ("doc.pdf", "is not one of"),
    ],
)
def test_bad_images_are_refused(tmp_path, src, message):
    (tmp_path / "doc.pdf").write_bytes(b"%PDF")
    with pytest.raises(ValueError, match=message):
        plan_article(f"![x]({src})", tmp_path)


def test_unsupported_things_are_reported(tmp_path):
    md = "| a | b |\n|---|---|\n| 1 | 2 |\n\n<div>raw</div>\n\nTexto."
    plan = plan_article(md, tmp_path)
    assert any("Tables" in w for w in plan.warnings)
    assert any("Raw HTML" in w for w in plan.warnings)
    assert "<div>" not in plan.blocks[0].html


def test_empty_body_is_refused(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        plan_article("   \n\n", tmp_path)


def test_html_inside_code_is_not_reported(tmp_path):
    plan = plan_article("```html\n<div>ejemplo</div>\n```", tmp_path)
    assert plan.warnings == []
    assert "&lt;div&gt;" in plan.blocks[0].html
