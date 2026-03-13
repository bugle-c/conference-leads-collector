from conference_leads_collector.extractors.conferences import extract_conference_data


HTML = """
<html>
  <body>
    <section>
      <h2>Спикеры</h2>
      <div class="speaker-card">
        <h3>Иван Петров</h3>
        <p>CMO, Rocket Labs</p>
      </div>
      <div class="speaker-card">
        <h3>Анна Сидорова</h3>
        <p>Head of Marketing, Data Fusion</p>
      </div>
    </section>
    <section>
      <h2>Спонсоры</h2>
      <div class="sponsor-card">
        <img alt="Alpha Cloud" src="/alpha.png" />
      </div>
      <div class="partner-card">
        <span>Beta AI</span>
      </div>
    </section>
  </body>
</html>
"""


def test_extract_conference_data_finds_speakers_and_sponsors() -> None:
    result = extract_conference_data("https://example.com/speakers", HTML)

    assert [speaker.full_name for speaker in result.speakers] == [
        "Иван Петров",
        "Анна Сидорова",
    ]
    assert result.speakers[0].title == "CMO"
    assert result.speakers[0].company == "Rocket Labs"
    assert [sponsor.name for sponsor in result.sponsors] == [
        "Alpha Cloud",
        "Beta AI",
    ]


NOISE_HTML = """
<html>
  <body>
    <section>
      <h2>Спонсоры</h2>
      <a href="/speakers">Спикеры</a>
      <a href="/program">Программа</a>
      <a href="/tickets">Купить билет</a>
      <a href="mailto:info@example.com">info@example.com</a>
      <p>Одно из самых значимых деловых мероприятий в области ИИ.</p>
    </section>
  </body>
</html>
"""


def test_extract_conference_data_ignores_navigation_noise_in_sponsors() -> None:
    result = extract_conference_data("https://example.com", NOISE_HTML)

    assert result.sponsors == []


SPONSOR_MIXED_HTML = """
<html>
  <body>
    <section>
      <h2>Партнеры</h2>
      <ul>
        <li>Архив</li>
        <li>Тарифы</li>
        <li>Партнерам</li>
        <li>Acme Cloud</li>
      </ul>
      <div class="partner-card">
        <img alt="North Star AI" src="/north-star.png" />
      </div>
    </section>
  </body>
</html>
"""


def test_extract_conference_data_keeps_only_real_sponsors_from_partner_sections() -> None:
    result = extract_conference_data("https://example.com", SPONSOR_MIXED_HTML)

    assert [sponsor.name for sponsor in result.sponsors] == ["Acme Cloud", "North Star AI"]


SPEAKER_NOISE_HTML = """
<html>
  <body>
    <section>
      <h2>Спикеры</h2>
      <div class="speaker-card"><h3>Registration Ru En</h3></div>
      <div class="speaker-card"><h3>Samsung Electronics</h3></div>
      <div class="speaker-card"><h3>Дмитрий Аникин</h3><p>CMO, Example</p></div>
    </section>
  </body>
</html>
"""


def test_extract_conference_data_ignores_noise_in_speakers() -> None:
    result = extract_conference_data("https://example.com", SPEAKER_NOISE_HTML)

    assert [speaker.full_name for speaker in result.speakers] == ["Дмитрий Аникин"]
