from conference_leads_collector.services.vision_extraction import VisionExtractor


def test_vision_extractor_parses_response():
    extractor = VisionExtractor.__new__(VisionExtractor)
    result = extractor._parse_response(
        '{"speakers": [{"full_name": "Jane Roe", "title": "CTO", "company": "Acme"}], '
        '"sponsors": [{"name": "BigCorp", "category": "partner"}]}'
    )
    assert len(result.speakers) == 1
    assert result.speakers[0].full_name == "Jane Roe"
    assert result.speakers[0].title == "CTO"
    assert result.speakers[0].company == "Acme"
    assert result.speakers[0].confidence == 92
    assert len(result.sponsors) == 1
    assert result.sponsors[0].name == "BigCorp"


def test_vision_extractor_handles_markdown_wrapped_json():
    extractor = VisionExtractor.__new__(VisionExtractor)
    result = extractor._parse_response(
        '```json\n{"speakers": [], "sponsors": [{"name": "X", "category": "sponsor"}]}\n```'
    )
    assert len(result.sponsors) == 1
    assert result.sponsors[0].name == "X"


def test_vision_extractor_returns_empty_on_bad_json():
    extractor = VisionExtractor.__new__(VisionExtractor)
    result = extractor._parse_response("not json at all")
    assert result.speakers == []
    assert result.sponsors == []


def test_vision_extractor_skips_empty_names():
    extractor = VisionExtractor.__new__(VisionExtractor)
    result = extractor._parse_response(
        '{"speakers": [{"full_name": "", "title": "CTO"}, {"full_name": "Jane Roe"}], "sponsors": [{"name": ""}]}'
    )
    assert len(result.speakers) == 1
    assert result.speakers[0].full_name == "Jane Roe"
    assert len(result.sponsors) == 0
