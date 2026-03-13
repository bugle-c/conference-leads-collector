from conference_leads_collector.extractors.tenchat import (
    extract_public_profile_urls,
    extract_tenchat_profile,
)


SEARCH_HTML = """
<html><body>
  <a href="https://tenchat.ru/media/12345-jane-smith">Jane Smith</a>
  <a href="https://example.com/other">Other</a>
</body></html>
"""

SEARCH_RSS = """<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0">
  <channel>
    <item><title>Jane</title><link>https://tenchat.ru/jane_smith</link></item>
    <item><title>Post</title><link>https://tenchat.ru/post/12345</link></item>
  </channel>
</rss>
"""

PROFILE_HTML = """
<html>
  <body>
    <h1>Jane Smith</h1>
    <div>Директор по маркетингу</div>
    <div>Подписчики: 1 540</div>
  </body>
</html>
"""


def test_extract_public_profile_urls() -> None:
    urls = extract_public_profile_urls(SEARCH_HTML)

    assert urls == ["https://tenchat.ru/media/12345-jane-smith"]


def test_extract_public_profile_urls_from_rss() -> None:
    urls = extract_public_profile_urls(SEARCH_RSS)

    assert urls == ["https://tenchat.ru/jane_smith", "https://tenchat.ru/post/12345"]


def test_extract_tenchat_profile() -> None:
    profile = extract_tenchat_profile("https://tenchat.ru/media/12345-jane-smith", PROFILE_HTML)

    assert profile.full_name == "Jane Smith"
    assert profile.job_title == "Директор по маркетингу"
    assert profile.followers == 1540
