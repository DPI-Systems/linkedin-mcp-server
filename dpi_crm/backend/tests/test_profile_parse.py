import datetime as dt

from app.services.profile_parse import parse_profile, parse_start
from tests.conftest import profile


def test_profile_gives_name_headline_employer_and_start_month():
    parsed = parse_profile(profile("Ana Ejemplo"), today=dt.date(2026, 12, 5))
    assert parsed == {
        "full_name": "Ana Ejemplo",
        "headline": "CTO | AI",
        "location": "Sevilla y alrededores",
        "title": "CTO",
        "company": "Acme",
        "company_since": dt.date(2025, 11, 1),
        "company_since_text": "nov. de 2025 - actualidad · 1 año",
        "company_linkedin_id": "42",
    }


def test_start_dates_in_spanish_english_and_year_only():
    assert parse_start("sept. de 2019") == dt.date(2019, 9, 1)
    assert parse_start("Aug 2021") == dt.date(2021, 8, 1)
    assert parse_start("2018") == dt.date(2018, 1, 1)


def test_a_month_outside_the_table_keeps_the_text_but_no_date():
    parsed = parse_profile(profile("Ana", start="Okt. 2024"))
    assert parsed["company_since"] is None
    assert parsed["company_since_text"].startswith("Okt. 2024")
    assert parsed["company"] == "Acme"


def test_a_profile_without_experience_dates_has_no_position():
    payload = profile("Ana")
    payload["sections"]["experience"] = "Experiencia\n\nNada que mostrar"
    parsed = parse_profile(payload)
    assert parsed["full_name"] == "Ana"
    assert "company" not in parsed and "company_since" not in parsed


GROUPED = {
    "sections": {
        "main_profile": "Rosa Ejemplo\n\n· 1er\n\nConsultora\n\nMadrid",
        "experience": "Experiencia\n\nMichael Page\n\n2 años 11 meses\n\nAssociate Manager\n\n"
        "nov. de 2026 - actualidad · 1 mes\n\nMadrid",
    },
    "references": {
        "experience": [
            {"kind": "company", "url": "/company/7/", "text": "Senior Consultant jul. de 2025"},
            {"kind": "company", "url": "/company/8/", "text": "Recruiter Otra SL"},
        ]
    },
}


def test_several_roles_at_one_employer_give_the_company_and_its_tenure():
    parsed = parse_profile(GROUPED, today=dt.date(2026, 12, 5))
    assert parsed["company"] == "Michael Page"
    assert parsed["title"] == "Associate Manager"
    # 2 years 11 months before December 2026, not the newest role's start.
    assert parsed["company_since"] == dt.date(2024, 1, 1)
    assert parsed["company_since_text"].startswith("2 años 11 meses · puesto actual: nov. de 2026")
    assert parsed["company_linkedin_id"] == "7"


def test_an_employer_line_without_duration_keeps_the_company_but_no_date():
    payload = {
        "sections": {
            "main_profile": "Eric\n\nConsultor",
            "experience": "Experiencia\n\nCapgemini\n\nJornada completa\n\nConsultor Senior\n\n"
            "oct. de 2026 - actualidad · 2 meses",
        }
    }
    parsed = parse_profile(payload, today=dt.date(2026, 12, 5))
    assert (parsed["company"], parsed["title"], parsed["company_since"]) == (
        "Capgemini",
        "Consultor Senior",
        None,
    )


def test_a_stored_profile_parses_the_same_as_the_live_one():
    from app.services.profile_parse import raw_for_storage

    today = dt.date(2026, 12, 5)
    assert parse_profile(raw_for_storage(GROUPED), today) == parse_profile(GROUPED, today)
