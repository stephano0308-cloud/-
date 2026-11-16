"""위치 정보 생성 모듈.

TextSegment의 위치 정보를 가독성 높은 location_hint로 변환한다.
"""

from hwpx_parser import TextSegment


def build_location_hint(segment: TextSegment) -> str:
    """TextSegment에서 사람이 읽기 쉬운 location_hint 문자열을 생성한다.

    예: "본문 > 문단 3 > 문장 1"
        "표 > 문단 5 > 문장 2"
    """
    parts: list[str] = []

    section_labels = {
        "body": "본문",
        "header": "머리말",
        "footer": "꼬리말",
        "footnote": "각주",
        "endnote": "미주",
        "table": "표",
    }
    parts.append(section_labels.get(segment.section, segment.section))

    if segment.paragraph_id and not segment.paragraph_id.startswith("fallback_"):
        parts.append(f"문단 {segment.paragraph_id}")
    else:
        parts.append(f"문단 {segment.paragraph_index + 1}")

    parts.append(f"문장 {segment.sentence_index + 1}")

    return " > ".join(parts)


def enrich_segments(segments: list[TextSegment]) -> list[TextSegment]:
    """세그먼트 리스트에 location_hint를 채워 넣는다."""
    for seg in segments:
        if not seg.location_hint:
            seg.location_hint = build_location_hint(seg)
    return segments
