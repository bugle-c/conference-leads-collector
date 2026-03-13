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


def test_extract_tenchat_profile() -> None:
    profile = extract_tenchat_profile("https://tenchat.ru/media/12345-jane-smith", PROFILE_HTML)

    assert profile.full_name == "Jane Smith"
    assert profile.job_title == "Директор по маркетингу"
    assert profile.followers == 1540
