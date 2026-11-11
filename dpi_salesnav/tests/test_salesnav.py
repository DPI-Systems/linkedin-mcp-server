"""Offline tests for the Sales Navigator extension (synthetic data only)."""

from urllib.parse import unquote

import pytest

from dpi_salesnav.capture import is_sales_api_response
from dpi_salesnav.inmail import validate_lead_url
from dpi_salesnav.people import (
    account_from_hit,
    LeadFilters,
    lead_from_hit,
    lead_search_url,
    region_breakdown,
)

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
    assert lead["promoted_recently"] is False
    assert lead["sales_nav_url"] == (
        "https://www.linkedin.com/sales/lead/ACwAAAexample,NAME_SEARCH,abcd"
    )


def test_lead_tolerates_missing_fields():
    lead = lead_from_hit({"fullName": "X"}, "42")
    assert lead["title"] is None
    assert lead["sales_nav_url"] is None
    assert lead["recent_activity"] is None


def test_search_url_double_encodes_company_urn():
    url = lead_search_url("42")
    assert "urn%253Ali%253Aorganization%253A42" in url
    assert "page=" not in url
    query = unquote(url.split("query=", 1)[1])
    assert "id:urn%3Ali%3Aorganization%3A42" in query


def test_search_url_keywords_are_sanitized_and_paged():
    url = lead_search_url("42", LeadFilters(keywords="CTO (Madrid), data"), 3)
    query = unquote(url.split("query=", 1)[1])
    assert query.startswith("(keywords:CTO Madrid data,filters:")
    assert url.endswith("&page=3")


def test_search_url_adds_each_filter():
    filters = LeadFilters(
        titles=("Responsable de licitaciones",),
        seniority=("director", "cxo"),
        functions=("sales", "business_development"),
        regions=("105646813",),
        posted_recently=True,
        changed_jobs_recently=True,
    )
    query = unquote(lead_search_url("42", filters).split("query=", 1)[1])
    assert (
        "(type:CURRENT_TITLE,values:List((text:Responsable de licitaciones,"
        "selectionType:INCLUDED)))"
    ) in query
    assert (
        "(type:SENIORITY_LEVEL,values:List((id:220,selectionType:INCLUDED),"
        "(id:310,selectionType:INCLUDED)))"
    ) in query
    assert "(type:FUNCTION,values:List((id:25," in query
    assert "(id:4,selectionType:INCLUDED)" in query
    assert "(type:REGION,values:List((id:105646813," in query
    assert "(type:POSTED_ON_LINKEDIN,values:List((id:RPOL," in query
    assert "(type:RECENTLY_CHANGED_JOBS,values:List((id:RPC," in query


@pytest.mark.parametrize(
    "kwargs",
    [{"seniority": ("boss",)}, {"functions": ("sales_ops",)}, {"regions": ("Spain",)}],
)
def test_filters_reject_unknown_values(kwargs):
    with pytest.raises(ValueError):
        LeadFilters(**kwargs)


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


def test_search_url_without_company():
    url = lead_search_url(None, LeadFilters(keywords="LangChain", regions=("123",)))
    query = unquote(url.split("query=", 1)[1])
    assert "CURRENT_COMPANY" not in query
    assert query == (
        "(keywords:LangChain,filters:List((type:REGION,values:List("
        "(id:123,selectionType:INCLUDED)))))"
    )
    assert unquote(
        lead_search_url(None, LeadFilters(keywords="x")).split("query=")[1]
    ) == ("(keywords:x)")


def test_lead_without_company_takes_first_position():
    lead = lead_from_hit(HIT, None)
    assert lead["title"] == "Consejera"


def test_region_breakdown_reads_geography_metadata():
    payload = {
        "metadata": {
            "filters": [
                {"singleFilterMetadata": {"type": "INDUSTRY", "values": []}},
                {
                    "aggregatedFilterMetadata": {
                        "type": "GEOGRAPHY",
                        "values": [
                            {
                                "type": "REGION",
                                "values": [
                                    {
                                        "id": "1",
                                        "displayValue": "Ciudad A",
                                        "displayCount": "12 ",
                                    },
                                    {
                                        "id": "2",
                                        "displayValue": "País B",
                                        "displayCount": "1,2K",
                                    },
                                ],
                            },
                            {"type": "POSTAL_CODE", "values": []},
                        ],
                    }
                },
            ]
        }
    }
    assert region_breakdown(payload) == [
        {"id": "1", "name": "Ciudad A", "count": 12},
        {"id": "2", "name": "País B", "count": "1,2K"},
    ]
    assert region_breakdown({}) == []


def test_account_from_hit_keeps_fields_to_choose_from():
    account = account_from_hit(
        {
            "entityUrn": "urn:li:fs_salesCompany:40533922",
            "companyName": "Empresa A SL Y Empresa B SL UTE",
            "industry": "Servicios medioambientales",
            "employeeCountRange": "De 11 a 50 empleados",
            "geoRegion": "Sevilla, Andalucía",
            "description": "x" * 400,
        }
    )
    assert account["company_id"] == "40533922"
    assert account["location"] == "Sevilla, Andalucía"
    assert len(account["description"]) == 300
    assert account["sales_nav_url"] == "https://www.linkedin.com/sales/company/40533922"
    assert account_from_hit({"entityUrn": "urn:li:organization:1"}) is None


def test_hip_challenge_is_reported_not_retried():
    from types import SimpleNamespace

    from dpi_salesnav.capture import SalesApiRecorder
    from dpi_salesnav.people import ChallengeShown, _raise_if_challenged

    recorder = SalesApiRecorder(page=None)
    response = SimpleNamespace(
        url="https://www.linkedin.com/help/api/hip/v2/initiate/sales-navigator",
        status=200,
    )
    recorder._handle_response(response)
    with pytest.raises(ChallengeShown):
        _raise_if_challenged(recorder)


def test_a_lead_page_counts_as_past_its_splash_by_title_or_text():
    from dpi_salesnav.inmail import _half_rendered

    splash = {"title": "Sales Navigator", "body_chars": 0}
    assert not _half_rendered(splash)
    assert not _half_rendered({**splash, "after_screenshot": splash})
    assert _half_rendered({"title": "Ana Ejemplo | Sales Navigator", "body_chars": 0})
    assert _half_rendered({**splash, "body_chars": 286})
    assert _half_rendered({**splash, "after_screenshot": {"body_chars": 12}})
    assert not _half_rendered({"error": "TimeoutError()"})


def test_renderer_cpu_is_reported_busiest_first(monkeypatch):
    from dpi_salesnav import page_health

    monkeypatch.setattr(page_health, "renderer_cpu", lambda: {7: 12.0, 8: 3.5})
    got = page_health.cpu_since({7: 2.0, 8: 3.0}, 20.0)
    assert got["renderers"] == 2
    assert got["busiest"][0] == {"pid": 7, "cpu_s": 10.0, "share": 0.5}
    assert got["busiest"][1]["cpu_s"] == 0.5
    monkeypatch.setattr(page_health, "renderer_cpu", dict)
    assert page_health.cpu_since({}, 1.0) == {"available": False}
