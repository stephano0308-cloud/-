"""HWPX 문서 파싱 모듈.

HWPX 파일(ZIP 포맷)을 열어 내부 XML을 파싱하고,
텍스트·구조·위치 정보를 추출한다.
"""

import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# HWPX 내부에서 사용되는 주요 네임스페이스
NAMESPACES = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hc": "http://www.hancom.co.kr/hwpml/2011/head",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "ho": "http://www.hancom.co.kr/hwpml/2011/office",
    "hp6": "urn:hancom:hwpml:office",
    "hp6p": "urn:hancom:hwpml:paragraph",
    "hp6s": "urn:hancom:hwpml:section",
    "opf": "http://www.idpf.org/2007/opf",
}


@dataclass
class TextSegment:
    """추출된 텍스트 조각 하나."""

    text: str
    file: str
    section: str  # body, header, footer, footnote, table, endnote
    paragraph_id: str
    paragraph_index: int
    sentence_index: int
    text_offset_start: int
    text_offset_end: int
    context_before: str = ""
    context_after: str = ""
    page: Optional[int] = None
    line: Optional[int] = None
    location_hint: str = ""
    is_table: bool = False


@dataclass
class DocumentStructure:
    """파싱된 문서 전체 구조."""

    segments: list[TextSegment] = field(default_factory=list)
    source_file: str = ""


def _register_namespaces():
    """ElementTree에 네임스페이스를 등록한다."""
    for prefix, uri in NAMESPACES.items():
        try:
            ET.register_namespace(prefix, uri)
        except Exception:
            pass


def _safe_parse_xml(xml_bytes: bytes, filename: str) -> Optional[ET.Element]:
    """XML을 안전하게 파싱한다. 실패해도 None을 반환하고 계속 진행."""
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        logger.warning("XML 파싱 실패 (%s): %s", filename, e)
        try:
            cleaned = xml_bytes.decode("utf-8", errors="replace")
            cleaned = re.sub(r"[^\x09\x0a\x0d\x20-\uffff]", "", cleaned)
            return ET.fromstring(cleaned.encode("utf-8"))
        except Exception:
            logger.warning("XML 복구 파싱도 실패 (%s)", filename)
            return None


def _extract_text_from_element(elem: ET.Element) -> str:
    """XML 요소에서 재귀적으로 텍스트를 추출한다."""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        tag_local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag_local in ("t", "T", "char", "CHAR"):
            if child.text:
                parts.append(child.text)
        elif tag_local in ("tab", "TAB"):
            parts.append("\t")
        elif tag_local in ("lineBreak", "LINEBREAK", "br"):
            parts.append("\n")
        else:
            parts.append(_extract_text_from_element(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _find_all_ns(elem: ET.Element, local_name: str) -> list[ET.Element]:
    """네임스페이스에 관계없이 로컬 이름으로 요소를 재귀 검색한다."""
    results: list[ET.Element] = []
    for child in elem.iter():
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag.lower() == local_name.lower():
            results.append(child)
    return results


def _split_sentences(text: str) -> list[str]:
    """텍스트를 문장 단위로 분리한다. 띄어쓰기 관련 처리는 하지 않는다."""
    if not text.strip():
        return []
    sentences = re.split(r"(?<=[.!?。])\s+", text)
    return [s for s in sentences if s.strip()]


def _detect_section_files(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """HWPX ZIP 내부에서 본문 섹션 XML 파일 목록을 찾는다.

    Returns:
        list of (zip_path, section_type) 튜플
    """
    section_files: list[tuple[str, str]] = []

    content_xml_candidates = [
        "META-INF/container.xml",
        "Contents/content.hpf",
        "content.hpf",
    ]

    for candidate in content_xml_candidates:
        if candidate in zf.namelist():
            try:
                data = zf.read(candidate)
                root = _safe_parse_xml(data, candidate)
                if root is not None:
                    for item in root.iter():
                        href = item.get("href", "") or item.get("full-path", "")
                        if href and ("section" in href.lower() or "body" in href.lower()):
                            if href in zf.namelist():
                                section_files.append((href, "body"))
                            else:
                                prefixed = "Contents/" + href
                                if prefixed in zf.namelist():
                                    section_files.append((prefixed, "body"))
            except Exception as e:
                logger.debug("콘텐츠 파일 파싱 중 오류 (%s): %s", candidate, e)

    if not section_files:
        for name in sorted(zf.namelist()):
            lower = name.lower()
            if lower.endswith(".xml"):
                if "section" in lower or "body" in lower:
                    section_files.append((name, "body"))
                elif "header" in lower:
                    section_files.append((name, "header"))
                elif "footer" in lower:
                    section_files.append((name, "footer"))
                elif "footnote" in lower or "endnote" in lower:
                    section_files.append((name, "footnote"))

    if not section_files:
        for name in sorted(zf.namelist()):
            if name.lower().endswith(".xml") and "meta" not in name.lower():
                section_files.append((name, "body"))

    return section_files


def _extract_paragraphs(
    root: ET.Element,
    file_path: str,
    section_type: str,
    include_table: bool = True,
) -> list[TextSegment]:
    """XML 루트에서 문단(paragraph) 요소를 찾아 TextSegment 리스트를 생성한다."""
    segments: list[TextSegment] = []

    para_elements = _find_all_ns(root, "p")
    if not para_elements:
        para_elements = _find_all_ns(root, "para")
    if not para_elements:
        para_elements = _find_all_ns(root, "paragraph")

    all_para_texts: list[str] = []
    for para in para_elements:
        text = _extract_text_from_element(para)
        all_para_texts.append(text)

    for para_idx, para in enumerate(para_elements):
        is_table = False
        parent_check = para
        tag_local = parent_check.tag.split("}")[-1] if "}" in parent_check.tag else parent_check.tag
        if tag_local.lower() in ("tc", "cell", "tablecell"):
            is_table = True

        if is_table and not include_table:
            continue

        raw_text = all_para_texts[para_idx]
        if not raw_text.strip():
            continue

        p_id = para.get("id", "") or para.get("paraPrIDRef", "") or str(para_idx)
        effective_section = "table" if is_table else section_type

        sentences = _split_sentences(raw_text)
        if not sentences:
            sentences = [raw_text]

        offset = 0
        for sent_idx, sentence in enumerate(sentences):
            start = raw_text.find(sentence, offset)
            if start == -1:
                start = offset
            end = start + len(sentence)

            ctx_before = ""
            ctx_after = ""
            if para_idx > 0 and all_para_texts[para_idx - 1].strip():
                ctx_before = all_para_texts[para_idx - 1].strip()[-60:]
            if para_idx < len(all_para_texts) - 1 and all_para_texts[para_idx + 1].strip():
                ctx_after = all_para_texts[para_idx + 1].strip()[:60]

            seg = TextSegment(
                text=sentence,
                file=file_path,
                section=effective_section,
                paragraph_id=p_id,
                paragraph_index=para_idx,
                sentence_index=sent_idx,
                text_offset_start=start,
                text_offset_end=end,
                context_before=ctx_before,
                context_after=ctx_after,
                is_table=is_table,
            )
            segments.append(seg)
            offset = end

    return segments


def _extract_all_text_fallback(root: ET.Element, file_path: str) -> list[TextSegment]:
    """파라그래프 추출이 안 될 경우, 모든 텍스트 노드를 직접 수집한다."""
    segments: list[TextSegment] = []
    all_text_parts: list[str] = []

    for elem in root.iter():
        if elem.text and elem.text.strip():
            all_text_parts.append(elem.text.strip())
        if elem.tail and elem.tail.strip():
            all_text_parts.append(elem.tail.strip())

    full_text = " ".join(all_text_parts)
    if not full_text.strip():
        return segments

    sentences = _split_sentences(full_text)
    if not sentences:
        sentences = [full_text]

    offset = 0
    for idx, sentence in enumerate(sentences):
        start = full_text.find(sentence, offset)
        if start == -1:
            start = offset
        end = start + len(sentence)
        seg = TextSegment(
            text=sentence,
            file=file_path,
            section="body",
            paragraph_id=f"fallback_{idx}",
            paragraph_index=idx,
            sentence_index=0,
            text_offset_start=start,
            text_offset_end=end,
        )
        segments.append(seg)
        offset = end

    return segments


def parse_hwpx(file_path: str, include_table: bool = True) -> DocumentStructure:
    """HWPX 파일을 파싱하여 DocumentStructure를 반환한다.

    Args:
        file_path: HWPX 파일 경로
        include_table: 표 내부 텍스트 포함 여부

    Returns:
        DocumentStructure 객체
    """
    _register_namespaces()
    doc = DocumentStructure(source_file=file_path)

    if not os.path.isfile(file_path):
        logger.error("파일을 찾을 수 없습니다: %s", file_path)
        return doc

    try:
        zf = zipfile.ZipFile(file_path, "r")
    except (zipfile.BadZipFile, Exception) as e:
        logger.error("ZIP 파일 열기 실패 (%s): %s", file_path, e)
        return doc

    with zf:
        section_files = _detect_section_files(zf)
        if not section_files:
            logger.warning("섹션 파일을 찾을 수 없습니다: %s", file_path)
            return doc

        logger.info("파싱 대상 섹션 %d개: %s", len(section_files), file_path)

        for xml_path, section_type in section_files:
            try:
                xml_bytes = zf.read(xml_path)
            except Exception as e:
                logger.warning("파일 읽기 실패 (%s): %s", xml_path, e)
                continue

            root = _safe_parse_xml(xml_bytes, xml_path)
            if root is None:
                continue

            paragraphs = _extract_paragraphs(root, xml_path, section_type, include_table)
            if not paragraphs:
                paragraphs = _extract_all_text_fallback(root, xml_path)

            doc.segments.extend(paragraphs)

    logger.info("총 %d개 텍스트 세그먼트 추출: %s", len(doc.segments), file_path)
    return doc
