"""HWPX 파일 리더 — ZIP 해제 후 XML에서 텍스트를 추출한다.

HWPX 구조:
  ├── META-INF/
  ├── Contents/
  │   ├── content.hpf   (매니페스트)
  │   ├── section0.xml  (본문 섹션)
  │   ├── section1.xml
  │   └── ...
  └── mimetype

섹션 XML에서 텍스트 노드(<hp:t>)를 추출하여 문단별로 반환한다.
페이지 번호는 <hp:p pageBreak="1"> 속성으로, 줄 번호는
<hp:linesegarray>/<hp:lineseg> 요소로 판별한다.
"""

import os
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET


@dataclass
class ParagraphInfo:
    """문단 텍스트와 위치 정보를 담는다."""
    text: str
    page: int       # 페이지 번호 (1-based)
    line: int       # 해당 페이지 내 시작 줄 번호 (1-based)
    line_count: int  # 이 문단이 차지하는 줄 수


# HWPX에서 사용하는 주요 네임스페이스
_NAMESPACES = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hp3": "urn:hancom:hwpml:paragraph",
    "hc": "http://www.hancom.co.kr/hwpml/2011/content",
    "hc3": "urn:hancom:hwpml:content",
}


def read_hwpx(filepath: str) -> list[str]:
    """HWPX 파일에서 텍스트를 문단 단위로 추출한다.

    Args:
        filepath: .hwpx 파일 경로

    Returns:
        문단 텍스트 리스트
    """
    infos = read_hwpx_with_position(filepath)
    return [info.text for info in infos]


def read_hwpx_with_position(filepath: str) -> list[ParagraphInfo]:
    """HWPX 파일에서 텍스트와 위치 정보를 함께 추출한다.

    Args:
        filepath: .hwpx 파일 경로

    Returns:
        ParagraphInfo 리스트 (텍스트 + 페이지/줄 정보)
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {filepath}")

    if not zipfile.is_zipfile(filepath):
        raise ValueError(f"유효한 HWPX(ZIP) 파일이 아닙니다: {filepath}")

    paragraphs: list[ParagraphInfo] = []

    with zipfile.ZipFile(filepath, "r") as zf:
        # 섹션 파일 찾기 (section0.xml, section1.xml, ...)
        section_files = sorted(
            name for name in zf.namelist()
            if re.match(r"Contents/section\d+\.xml$", name, re.IGNORECASE)
        )

        if not section_files:
            # 다른 경로 패턴도 시도
            section_files = sorted(
                name for name in zf.namelist()
                if name.lower().endswith(".xml")
                and "section" in name.lower()
            )

        if not section_files:
            raise ValueError(
                "HWPX 파일에서 섹션 파일을 찾을 수 없습니다. "
                f"포함된 파일: {zf.namelist()}"
            )

        # 이전 섹션까지의 페이지 수를 누적
        current_page = 1
        for section_file in section_files:
            with zf.open(section_file) as f:
                section_paras, last_page = _parse_section_xml_with_position(
                    f.read(), current_page
                )
                paragraphs.extend(section_paras)
                current_page = last_page

    return paragraphs


def _parse_section_xml_with_position(
    xml_bytes: bytes, start_page: int
) -> tuple[list[ParagraphInfo], int]:
    """섹션 XML을 파싱하여 문단 텍스트와 위치 정보를 반환한다.

    Returns:
        (ParagraphInfo 리스트, 마지막 페이지 번호)
    """
    root = ET.fromstring(xml_bytes)
    ns_map = _detect_namespaces(root)

    paragraphs: list[ParagraphInfo] = []
    current_page = start_page
    current_line_in_page = 1

    # 문단(<hp:p>) 요소 탐색
    para_tags = _find_all_tags(root, "p", ns_map)

    for para_elem in para_tags:
        # pageBreak 속성 확인
        page_break = para_elem.get("pageBreak", "0")
        if page_break == "1" or page_break.lower() == "true":
            current_page += 1
            current_line_in_page = 1

        # 텍스트 추출
        texts = []
        text_tags = _find_all_tags(para_elem, "t", ns_map)
        for t_elem in text_tags:
            if t_elem.text:
                texts.append(t_elem.text)
            if t_elem.tail:
                texts.append(t_elem.tail)

        if not texts:
            texts = list(_iter_text(para_elem))

        para_text = "".join(texts).strip()

        # lineseg 개수로 줄 수 계산
        line_count = _count_lines(para_elem, ns_map)

        if para_text:
            paragraphs.append(ParagraphInfo(
                text=para_text,
                page=current_page,
                line=current_line_in_page,
                line_count=max(1, line_count),
            ))

        # 줄 수만큼 현재 줄 번호 증가
        current_line_in_page += max(1, line_count)

    return paragraphs, current_page


def _count_lines(para_elem: ET.Element, ns_map: dict[str, str]) -> int:
    """문단 내 lineseg 요소 수를 세어 줄 수를 반환한다."""
    # linesegarray 내의 lineseg 개수
    count = 0
    lineseg_arrays = _find_all_tags(para_elem, "linesegarray", ns_map)
    for lsa in lineseg_arrays:
        linesegs = _find_all_tags(lsa, "lineseg", ns_map)
        count += len(linesegs)

    # lineseg가 없으면 직접 lineseg 검색
    if count == 0:
        linesegs = _find_all_tags(para_elem, "lineseg", ns_map)
        count = len(linesegs)

    return count if count > 0 else 1


def _parse_section_xml(xml_bytes: bytes) -> list[str]:
    """섹션 XML을 파싱하여 문단 텍스트 리스트를 반환한다."""
    root = ET.fromstring(xml_bytes)

    # 네임스페이스를 자동 감지
    ns_map = _detect_namespaces(root)

    paragraphs = []

    # 문단(<hp:p> 또는 <p>) 요소 탐색
    para_tags = _find_all_tags(root, "p", ns_map)

    for para_elem in para_tags:
        texts = []
        # 텍스트 런(<hp:t> 또는 <t>) 추출
        text_tags = _find_all_tags(para_elem, "t", ns_map)
        for t_elem in text_tags:
            if t_elem.text:
                texts.append(t_elem.text)
            # tail 텍스트도 포함
            if t_elem.tail:
                texts.append(t_elem.tail)

        # <hp:t>가 없으면 모든 텍스트 노드를 수집
        if not texts:
            texts = list(_iter_text(para_elem))

        para_text = "".join(texts).strip()
        if para_text:
            paragraphs.append(para_text)

    return paragraphs


def _detect_namespaces(root: ET.Element) -> dict[str, str]:
    """XML 루트에서 실제 사용 중인 네임스페이스를 감지한다."""
    ns_map = {}
    tag = root.tag
    # {namespace}localname 형식에서 namespace 추출
    if tag.startswith("{"):
        ns_uri = tag[1:tag.index("}")]
        ns_map["root"] = ns_uri

    # 모든 요소를 순회하며 네임스페이스 수집
    for elem in root.iter():
        if elem.tag.startswith("{"):
            ns_uri = elem.tag[1:elem.tag.index("}")]
            local = elem.tag[elem.tag.index("}") + 1:]
            if local in ("p", "t", "run", "text", "linesegarray", "lineseg"):
                ns_map[local] = ns_uri

    return ns_map


def _find_all_tags(root: ET.Element, local_name: str,
                   ns_map: dict[str, str]) -> list[ET.Element]:
    """지정된 로컬 이름의 모든 요소를 찾는다 (네임스페이스 무관)."""
    results = []

    # 알려진 네임스페이스로 검색
    for prefix, uri in _NAMESPACES.items():
        found = root.findall(f".//{{{uri}}}{local_name}")
        results.extend(found)

    # 감지된 네임스페이스로도 검색
    if local_name in ns_map:
        found = root.findall(f".//{{{ns_map[local_name]}}}{local_name}")
        results.extend(found)

    # 네임스페이스 없이도 검색
    found = root.findall(f".//{local_name}")
    results.extend(found)

    # 중복 제거 (순서 유지)
    seen = set()
    unique = []
    for elem in results:
        elem_id = id(elem)
        if elem_id not in seen:
            seen.add(elem_id)
            unique.append(elem)

    return unique


def _iter_text(elem: ET.Element):
    """요소 내 모든 텍스트를 재귀적으로 수집한다."""
    if elem.text:
        yield elem.text
    for child in elem:
        yield from _iter_text(child)
        if child.tail:
            yield child.tail


def read_text_file(filepath: str) -> list[str]:
    """일반 텍스트 파일(.txt)을 문단 단위로 읽는다.

    테스트 용도 또는 텍스트 파일 직접 검사에 사용한다.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 빈 줄을 기준으로 문단 분리
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content)]
    return [p for p in paragraphs if p]


def read_text_file_with_position(filepath: str) -> list[ParagraphInfo]:
    """일반 텍스트 파일을 ParagraphInfo 형태로 읽는다."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content)]
    result = []
    current_line = 1
    for p in paragraphs:
        if p:
            line_count = p.count("\n") + 1
            result.append(ParagraphInfo(
                text=p,
                page=1,  # 텍스트 파일은 페이지 개념 없음
                line=current_line,
                line_count=line_count,
            ))
            current_line += line_count + 1  # +1 for blank line
    return result
