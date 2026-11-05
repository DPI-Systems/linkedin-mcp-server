"""Offline tests for reading the contact-info dialog (synthetic data only)."""

import pytest

from dpi_salesnav.contact_info import contact_from_dialog, validate_username

TEXT = """Información de contacto

Perfil de Ana

linkedin.com/in/ana-ejemplo

Sitio web

ana.example (blog)

Teléfono

600 11 22 33 (móvil)

+34 954 000 000

Email

ana@example.com

Cumpleaños

4 de noviembre

Contacto desde

15 de mar. de 2026
"""
HREFS = [
    "https://www.linkedin.com/in/ana-ejemplo/",
    "https://www.linkedin.com/safety/go/?url=http%3A%2F%2Fana%2Eexample%2F&urlhash=x",
    "mailto:ana@example.com",
    "mailto:ana@example.com",
]


def test_emails_websites_and_phones_come_from_structure_not_labels():
    out = contact_from_dialog(TEXT, HREFS)
    assert out["emails"] == ["ana@example.com"]
    assert out["websites"] == ["http://ana.example/"]
    assert out["phones"] == [
        {"number": "600 11 22 33", "kind": "móvil"},
        {"number": "+34 954 000 000", "kind": None},
    ]
    assert "Cumpleaños" in out["text"]


def test_dates_and_short_numbers_are_not_phones():
    out = contact_from_dialog("15 de mar. de 2026\n2024\n12345\n4 de noviembre", [])
    assert out["phones"] == []


def test_an_empty_dialog_gives_empty_lists():
    assert contact_from_dialog("Información de contacto", []) == {
        "emails": [],
        "phones": [],
        "websites": [],
        "text": "Información de contacto",
    }


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ana-ejemplo", "ana-ejemplo"),
        ("/in/jos%C3%A9-p/", "josé-p"),
        ("in/bob/", "bob"),
    ],
)
def test_usernames_are_normalized(raw, expected):
    assert validate_username(raw) == expected


def test_a_url_is_not_a_username():
    with pytest.raises(ValueError):
        validate_username("https://www.linkedin.com/in/ana/")
