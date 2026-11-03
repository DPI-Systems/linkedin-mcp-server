"""Offline tests for the Sales Navigator extension (synthetic data only)."""

from urllib.parse import unquote

import pytest

from dpi_salesnav.capture import is_sales_api_response
from dpi_salesnav.inmail import validate_lead_url
from dpi_salesnav.people import lead_from_hit, lead_search_url

HIT = {
    "fullName": "Ana Ejemplo ",
    "firstName": "Ana",
    "geoRegion": "Sevilla, Andalucía, España",
    "degree": 2,
    "openLink": False,
    "premium": True,
    "pendingInvitation": False,
    "saved": False,
    "objectUrn": "urn:li:member:1",
    "entityUrn": "urn:li:fs_salesProfile:(ACwAAAexample,NAME_SEARCH,abcd)",
    "summary": "Resumen.",
    "seniorityV2s": [{"displayName": "Director"}],
    "currentPositions": [
        {
            "title": "Consejera",
            "companyName": "Otra SA",
            "companyUrn": "urn:li:fs_salesCompany:999",
        },
        {
            "title": "Directora de Operaciones ",
            "companyName": "Empresa SL",
            "companyUrn": "urn:li:fs_salesCompany:42",
            "tenureAtCompany": {"numYears": 5, "numMonths": 2},
            "tenureAtPosition": {"numYears": 1},
            "startedOn": {"month": 3, "year": 2024},
        },
    ],
    "spotlightBadges": [
        {"id": "POSTED_ON_LINKEDIN", "displayValue": "2 Publicaciones recientes"},
        {"id": "RECENTLY_HIRED", "displayValue": "Nuevo puesto"},
    ],
}


def test_lead_prefers_position_at_searched_company():
    lead = lead_from_hit(HIT, "42")
    assert lead["full_name"] == "Ana Ejemplo"
    assert lead["title"] == "Directora de Operaciones"
    assert lead["company"] == "Empresa SL"
    assert lead["tenure_at_company"] == {"years": 5, "months": 2}
    assert lead["tenure_at_position"] == {"years": 1, "months": 0}
    assert lead["position_started_on"] == "2024-03"
    assert lead["other_current_positions"] == [
        {"title": "Consejera", "company": "Otra SA"}
    ]
    assert lead["recent_activity"] == "2 Publicaciones recientes"
    assert lead["changed_jobs_recently"] is True
    assert lead["sales_nav_url"] == (
        "https://www.linkedin.com/sales/lead/ACwAAAexample,NAME_SEARCH,abcd"
    )


def test_lead_tolerates_missing_fields():
    lead = lead_from_hit({"fullName": "X"}, "42")
    assert lead["title"] is None
    assert lead["sales_nav_url"] is None
    assert lead["recent_activity"] is None


def test_search_url_double_encodes_company_urn():
    url = lead_search_url("42", None, 1)
    assert "urn%253Ali%253Aorganization%253A42" in url
    assert "page=" not in url
    query = unquote(url.split("query=", 1)[1])
    assert "id:urn%3Ali%3Aorganization%3A42" in query


def test_search_url_keywords_are_sanitized_and_paged():
    url = lead_search_url("42", "CTO (Madrid), data", 3)
    query = unquote(url.split("query=", 1)[1])
    assert query.startswith("(keywords:CTO  Madrid   data,filters:")
    assert url.endswith("&page=3")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/sales/lead/ACw1,NAME_SEARCH,ab",
        "https://www.linkedin.com/sales/lead/ACw1,NAME_SEARCH,ab/",
    ],
)
def test_validate_lead_url_accepts_lead_pages(url):
    assert validate_lead_url(url) == (
        "https://www.linkedin.com/sales/lead/ACw1,NAME_SEARCH,ab"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/someone/",
        "https://evil.example/sales/lead/ACw1,NAME_SEARCH,ab",
        "https://www.linkedin.com/sales/lead/ACw1",
    ],
)
def test_validate_lead_url_rejects_others(url):
    with pytest.raises(ValueError):
        validate_lead_url(url)


def test_only_sales_api_responses_are_captured():
    assert is_sales_api_response(
        "https://www.linkedin.com/sales-api/salesApiLeadSearch?q=1"
    )
    assert not is_sales_api_response("https://www.linkedin.com/voyager/api/x")
    assert not is_sales_api_response("https://example.com/sales-api/x")
