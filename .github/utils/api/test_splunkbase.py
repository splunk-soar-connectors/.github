from unittest.mock import Mock

import pytest

from .splunkbase import (
    POST_WITH_FILES_NUM_RETRIES,
    SplunkbaseResponseError,
    USER_AGENT,
    _response_json,
    _retrying_session,
)


def test_retrying_session_retries_transient_gets_and_posts():
    session = _retrying_session()
    retry = session.get_adapter("https://").max_retries

    assert session.headers["User-Agent"] == USER_AGENT
    assert retry.total == POST_WITH_FILES_NUM_RETRIES
    assert retry.allowed_methods == frozenset(["GET", "POST"])
    assert {403, 429, 500, 502, 503, 504} <= set(retry.status_forcelist)
    assert retry.respect_retry_after_header is True


def test_response_json_rejects_missing_collection_fields():
    response = Mock(ok=True)
    response.json.return_value = {"detail": "temporarily unavailable"}

    with pytest.raises(SplunkbaseResponseError, match="results"):
        _response_json(response, required_keys={"results"})


def test_response_json_rejects_non_json_response():
    response = Mock(ok=True)
    response.json.side_effect = ValueError("not json")

    with pytest.raises(SplunkbaseResponseError, match="non-JSON"):
        _response_json(response)
