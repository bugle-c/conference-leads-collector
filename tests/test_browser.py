from conference_leads_collector.services.browser import BrowserRenderer, RenderedPage


def test_browser_renderer_returns_rendered_pages():
    renderer = BrowserRenderer()
    assert renderer is not None
    assert renderer.max_subpages == 3
    assert renderer.screenshot_width == 1280


def test_rendered_page_dataclass():
    page = RenderedPage(url="https://example.com", html="<html></html>", screenshot_b64="abc123")
    assert page.url == "https://example.com"
    assert page.status == 200


def test_discover_subpages_finds_speaker_links():
    renderer = BrowserRenderer()
    html = """
    <html><body>
      <a href="/speakers">Спикеры</a>
      <a href="/program">Программа</a>
      <a href="/about">О нас</a>
      <a href="https://other-site.com/speakers">External</a>
    </body></html>
    """
    urls = renderer._discover_subpages("https://example.com", html)
    assert "https://example.com/speakers" in urls
    assert "https://example.com/program" in urls
    assert "https://example.com/about" not in urls  # no keyword
    assert not any("other-site.com" in u for u in urls)  # external
