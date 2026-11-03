"""Offline tests for the Sales Navigator inbox mapping (synthetic data only)."""

import pytest

from dpi_salesnav.inbox import (
    thread_from_payload,
    validate_thread_id,
    viewer_profile_id,
    _people,
)

ME = "urn:li:fs_salesProfile:(ACwMe, , )"
ME_IN_THREAD = "urn:li:fs_salesProfile:(ACwMe,NAME_SEARCH,aa)"
THEM = "urn:li:fs_salesProfile:(ACwThem,NAME_SEARCH,bb)"
PAYLOAD = {
    "included": [
        {
            "entityUrn": THEM,
            "fullName": "Ana Ejemplo",
            "degree": 2,
            "objectUrn": "urn:li:member:2",
        }
    ],
}
NAV = {
    "url": "https://www.linkedin.com/sales-api/salesApiNavChrome?x",
    "json": {"member": ME},
}


def _thread(*messages):
    return {
        "id": "2-abc==",
        "participants": [THEM, ME_IN_THREAD],
        "unreadMessageCount": 1,
        "totalMessageCount": len(messages),
        "messages": list(messages),
    }


def test_viewer_comes_from_nav_chrome():
    assert viewer_profile_id([NAV]) == "ACwMe"
    assert viewer_profile_id([]) is None


def test_thread_awaiting_reply_when_last_message_is_ours():
    t = thread_from_payload(
        _thread(
            {
                "author": ME_IN_THREAD,
                "type": "INMAIL",
                "body": "Hola",
                "deliveredAt": 1000,
            },
        ),
        _people(PAYLOAD),
        "ACwMe",
    )
    assert t["contact"]["full_name"] == "Ana Ejemplo"
    assert t["contact"]["sales_nav_url"] == (
        "https://www.linkedin.com/sales/lead/ACwThem,NAME_SEARCH,bb"
    )
    assert t["awaiting_their_reply"] is True
    assert t["they_replied"] is False
    assert t["last_message"]["from_me"] is True


def test_reply_is_detected_and_messages_are_oldest_first():
    t = thread_from_payload(
        _thread(
            {
                "author": THEM,
                "type": "INMAIL_REPLY",
                "body": "Me interesa",
                "deliveredAt": 2000,
            },
            {
                "author": ME_IN_THREAD,
                "type": "INMAIL",
                "body": "Hola",
                "deliveredAt": 1000,
            },
        ),
        _people(PAYLOAD),
        "ACwMe",
    )
    assert [m["body"] for m in t["messages"]] == ["Hola", "Me interesa"]
    assert t["they_replied"] is True
    assert t["awaiting_their_reply"] is False


def test_unknown_viewer_does_not_invent_replies():
    t = thread_from_payload(
        _thread(
            {
                "author": ME_IN_THREAD,
                "type": "MESSAGE",
                "body": "Hola",
                "deliveredAt": 1,
            }
        ),
        _people(PAYLOAD),
        None,
    )
    assert t["they_replied"] is False


@pytest.mark.parametrize("bad", ["", "abc", "2-abc/../x", "https://x"])
def test_validate_thread_id_rejects_garbage(bad):
    with pytest.raises(ValueError):
        validate_thread_id(bad)


def test_validate_thread_id_accepts_encoded():
    assert validate_thread_id("2-YWJj%3D%3D") == "2-YWJj=="
