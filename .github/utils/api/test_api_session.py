from . import ApiSession


def test_api_session_retries_gets_and_posts():
    session = ApiSession("https://example.com")
    retry = session.get_adapter("https://example.com").max_retries

    assert retry.allowed_methods == frozenset(["GET", "POST"])
