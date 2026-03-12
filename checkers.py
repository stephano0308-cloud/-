"""HWPX 문서 검사 모듈 - 검사 규칙"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hwpx_reader import ParagraphInfo


@dataclass
class CheckResult:
    """검사 결과 하나를 나타낸다."""
    rule: str          # 검사 규칙 이름
    severity: str      # error / warning
    message: str       # 설명
    paragraph: int     # 문단 번호 (1-based)
    text: str          # 해당 문단 원문 (일부)
    page: int = 0      # 페이지 번호 (1-based, 0=알 수 없음)
    line: int = 0      # 줄 번호 (1-based, 0=알 수 없음)


# ──────────────────────────────────────────────
# 1. 오탈자 (흔한 한국어 오탈자 패턴)
# ──────────────────────────────────────────────
_COMMON_TYPOS: dict[str, str] = {
    "됬": "됐",
    "댔": "됐",
    "안됀": "안 된",
    "되어지다": "되다",
    "되여": "되어",
    "되어지는": "되는",
    "되어지고": "되고",
    "되어진": "된",
    "되어져": "되어",
    "몇일": "며칠",
    "몇몇일": "며칠",
    "어의없": "어이없",
    "어의가 없": "어이가 없",
    "금새": "금세",
    "오랫만": "오랜만",
    "오랫동": "오랫동",  # 오랫동안은 맞음 — 아래서 예외 처리
    "왠지": "웬지→왠지(O) / 웬지(X)",  # 왠지는 맞고 웬지가 틀림
    "웬지": "왠지",
    "웬만하": "왠만하→웬만하",
    "왠만하": "웬만하",
    "희안하": "희한하",
    "의외로": "의외로",  # placeholder - 실제 검사 제외
    "문안하": "문안하",
    "일일히": "일일이",
    "깨끗히": "깨끗이",
    "간간히": "간간이",
    "뜻깊히": "뜻깊이",
    "바램": "바람",
    "설레임": "설렘",
    "욕심쟁이": "욕심쟁이",
    "갯수": "개수",
    "갯": "개",  # 갯수 등
    "핼쑥": "핼쓱",
    "하겟": "하겠",
    "잇다": "있다",
    "엇다": "없다",  # 맥락에 따라 다를 수 있음
    "됄": "될",
    "안돼요": "안 돼요",
    "않되": "안 되",
    "않돼": "안 돼",
    # 추가 오탈자 패턴
    "도움말": "도움말",  # 올바름
    "것으로": "것으로",  # 올바름
    "수있다": "수 있다",
    "것만": "것만",  # 올바름
    "가능하": "가능하",  # 올바름
    "때": "때",  # 올바름
    "다음": "다음",  # 올바름
    "그러나": "그러나",  # 올바름
    "또한": "또한",  # 올바름
    "이외": "이외",  # 올바름
    "별도": "별도",  # 올바름
    "다만": "다만",  # 올바름
    "이어": "이어",  # 올바름
    "주": "주",  # 올바름
    "기": "기",  # 올바름
    "능": "능",  # 올바름
}

# 오탈자 검사에서 제외할 패턴 (예: '오랫동안'은 올바름)
_TYPO_EXCEPTIONS = {"오랫동안"}


def check_typos(paragraphs: list[str]) -> list[CheckResult]:
    """흔한 한국어 오탈자 패턴을 검사한다."""
    results = []
    for idx, para in enumerate(paragraphs, 1):
        for wrong, correct in _COMMON_TYPOS.items():
            if wrong == correct:
                continue
            positions = [m.start() for m in re.finditer(re.escape(wrong), para)]
            for pos in positions:
                # 예외 확인
                context = para[max(0, pos - 5):pos + len(wrong) + 5]
                if any(exc in context for exc in _TYPO_EXCEPTIONS):
                    continue
                results.append(CheckResult(
                    rule="오탈자",
                    severity="error",
                    message=f"'{wrong}' → '{correct}'",
                    paragraph=idx,
                    text=_snippet(para, pos, len(wrong)),
                ))
    return results


# ──────────────────────────────────────────────
# 2. 띄어쓰기 일관성 검사
# ──────────────────────────────────────────────
# 같은 단어가 다르게 띄어쓰기되는 경우만 체크
def check_spacing_consistency(paragraphs: list[str]) -> list[CheckResult]:
    """같은 단어가 다르게 띄어쓰기된 경우만 검사한다.

    예: '세무조사 기간연장통지서' vs '세무조사기간연장통지서'
    """
    results = []

    # 1단계: 모든 한글 어절(단어)을 추출하여 n-gram 생성
    # 각 문단에서 연속된 한글 단어 2~6개의 조합을 만듦
    # spacing_variants: {정규화된문자열: [(문단번호, 위치, 원본형태), ...]}
    spacing_variants: dict[str, list[tuple[int, int, str]]] = {}

    for idx, para in enumerate(paragraphs, 1):
        # 한글 어절 위치 추출
        words = list(re.finditer(r"[가-힣]+", para))

        # 연속 단어 n-gram (2~6개 조합)
        for n in range(2, min(7, len(words) + 1)):
            for i in range(len(words) - n + 1):
                group = words[i:i + n]
                start = group[0].start()
                end = group[-1].end()
                phrase = para[start:end]
                normalized = phrase.replace(" ", "")

                if len(normalized) < 4:  # 4글자 미만은 무시
                    continue

                if normalized not in spacing_variants:
                    spacing_variants[normalized] = []
                spacing_variants[normalized].append((idx, start, phrase))

    # 2단계: 같은 정규화 문자열에 서로 다른 형태가 있으면 경고
    reported: set[tuple[int, int]] = set()  # (문단, 위치) 중복 방지
    for normalized, variants in spacing_variants.items():
        unique_forms = set(v[2] for v in variants)
        if len(unique_forms) <= 1:
            continue

        forms = list(unique_forms)
        for para_idx, pos, phrase in variants:
            key = (para_idx, pos)
            if key in reported:
                continue
            reported.add(key)
            other_forms = [f for f in forms if f != phrase]
            results.append(CheckResult(
                rule="띄어쓰기 일관성",
                severity="warning",
                message=f"'{phrase}' — 문서 내 다른 표기: {', '.join(other_forms)}",
                paragraph=para_idx,
                text=_snippet(paragraphs[para_idx - 1], pos, len(phrase)),
            ))

    return results


# ──────────────────────────────────────────────
# 3. 제○조 / 제○항 형식 검사
# ──────────────────────────────────────────────
_ARTICLE_PATTERN = re.compile(
    r"제\s*(\d+)\s*조"
    r"|제\s*(\d+)\s*항"
    r"|제\s*(\d+)\s*호"
)

# 올바른 형식: "제1조", "제2항", "제3호" (숫자 앞뒤에 공백 없음)
_ARTICLE_STRICT = re.compile(r"제(\d+)(조|항|호)")


def check_article_format(paragraphs: list[str]) -> list[CheckResult]:
    """'제○조/항/호' 형식이 올바른지 검사한다."""
    results = []
    for idx, para in enumerate(paragraphs, 1):
        for m in _ARTICLE_PATTERN.finditer(para):
            matched = m.group()
            # 숫자와 조/항/호 사이 또는 '제'와 숫자 사이에 공백이 있으면 오류
            if not _ARTICLE_STRICT.match(matched.replace(" ", "")):
                continue  # 파싱 실패
            if " " in matched:
                clean = matched.replace(" ", "")
                results.append(CheckResult(
                    rule="조항 형식",
                    severity="error",
                    message=f"'{matched}' → '{clean}' (공백 제거)",
                    paragraph=idx,
                    text=_snippet(para, m.start(), len(matched)),
                ))

        # 한글 숫자로 쓴 경우 검사 ("제일조", "제이항" 등)
        for m in re.finditer(r"제([일이삼사오육칠팔구십백천]+)(조|항|호)", para):
            results.append(CheckResult(
                rule="조항 형식",
                severity="warning",
                message=f"'{m.group()}' — 아라비아 숫자 사용 권장 (예: 제1조)",
                paragraph=idx,
                text=_snippet(para, m.start(), len(m.group())),
            ))
    return results


# ──────────────────────────────────────────────
# 4. 숫자 표기 오류
# ──────────────────────────────────────────────
def check_number_format(paragraphs: list[str]) -> list[CheckResult]:
    """숫자 표기 오류를 검사한다."""
    results = []
    for idx, para in enumerate(paragraphs, 1):
        # 4자리 이상 숫자에 쉼표 없는 경우 (금액 등)
        for m in re.finditer(r"(?<![.,\d])(\d{4,})(?![.,\d])", para):
            num_str = m.group(1)
            # 연도(19xx, 20xx)나 조항 번호는 제외
            if re.match(r"^(19|20)\d{2}$", num_str):
                continue
            # 제N조 등 법조문 번호 뒤 숫자 제외
            before = para[max(0, m.start() - 2):m.start()]
            if re.search(r"제$", before):
                continue
            num = int(num_str)
            if num >= 10000:
                formatted = f"{num:,}"
                results.append(CheckResult(
                    rule="숫자 표기",
                    severity="warning",
                    message=f"'{num_str}' → '{formatted}' (천 단위 쉼표 권장)",
                    paragraph=idx,
                    text=_snippet(para, m.start(), len(num_str)),
                ))

        # 단위와 숫자 사이 띄어쓰기 검사 (예: "100만원" → "100만 원")
        for m in re.finditer(r"(\d+)(만|억|조)\s*(원|달러|엔|위안)", para):
            full = m.group()
            if " " not in full[len(m.group(1)):]:
                # "원" 앞에 공백이 없는 경우
                corrected = f"{m.group(1)}{m.group(2)} {m.group(3)}"
                if corrected != full:
                    results.append(CheckResult(
                        rule="숫자 표기",
                        severity="warning",
                        message=f"'{full}' → '{corrected}' (단위 띄어쓰기)",
                        paragraph=idx,
                        text=_snippet(para, m.start(), len(full)),
                    ))
    return results


# ──────────────────────────────────────────────
# 5. 날짜 형식 오류
# ──────────────────────────────────────────────
_DATE_PATTERNS = [
    # YYYY.MM.DD or YYYY-MM-DD or YYYY/MM/DD
    re.compile(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"),
    # YYYY년 MM월 DD일
    re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
]


def check_date_format(paragraphs: list[str]) -> list[CheckResult]:
    """날짜 형식 오류를 검사한다."""
    results = []
    for idx, para in enumerate(paragraphs, 1):
        for pat in _DATE_PATTERNS:
            for m in pat.finditer(para):
                year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                errors = []
                if month < 1 or month > 12:
                    errors.append(f"월({month})이 유효하지 않음")
                if day < 1 or day > 31:
                    errors.append(f"일({day})이 유효하지 않음")
                if month in (4, 6, 9, 11) and day > 30:
                    errors.append(f"{month}월은 30일까지")
                if month == 2 and day > 29:
                    errors.append(f"2월은 29일까지")
                if year < 1900 or year > 2100:
                    errors.append(f"연도({year})가 유효 범위 밖")

                for err in errors:
                    results.append(CheckResult(
                        rule="날짜 형식",
                        severity="error",
                        message=f"'{m.group()}' — {err}",
                        paragraph=idx,
                        text=_snippet(para, m.start(), len(m.group())),
                    ))

        # 날짜 구분자 혼용 검사 (예: 2024.01-15)
        for m in re.finditer(r"\d{4}([.\-/])\d{1,2}([.\-/])\d{1,2}", para):
            if m.group(1) != m.group(2):
                results.append(CheckResult(
                    rule="날짜 형식",
                    severity="warning",
                    message=f"'{m.group()}' — 날짜 구분자가 통일되지 않음",
                    paragraph=idx,
                    text=_snippet(para, m.start(), len(m.group())),
                ))
    return results


# ──────────────────────────────────────────────
# 6. 괄호 짝 오류
# ──────────────────────────────────────────────
_BRACKET_PAIRS = {
    "(": ")", ")": "(",
    "[": "]", "]": "[",
    "{": "}", "}": "{",
    "「": "」", "」": "「",
    "『": "』", "』": "『",
    "【": "】", "】": "【",
    "〈": "〉", "〉": "〈",
    "《": "》", "》": "《",
}
_OPEN_BRACKETS = set("([{「『【〈《")
_CLOSE_BRACKETS = set(")]}」』】〉》")


def check_brackets(paragraphs: list[str]) -> list[CheckResult]:
    """괄호 짝이 맞는지 검사한다."""
    results = []
    # 전체 텍스트에 대해 검사 (문단 단위로는 의미 없을 수 있음)
    full_text = "\n".join(paragraphs)
    para_offsets = []
    offset = 0
    for p in paragraphs:
        para_offsets.append(offset)
        offset += len(p) + 1  # +1 for \n

    stack: list[tuple[str, int]] = []  # (bracket, position)
    for i, ch in enumerate(full_text):
        if ch in _OPEN_BRACKETS:
            stack.append((ch, i))
        elif ch in _CLOSE_BRACKETS:
            expected_open = _BRACKET_PAIRS[ch]
            if stack and stack[-1][0] == expected_open:
                stack.pop()
            else:
                para_num = _pos_to_para(i, para_offsets)
                results.append(CheckResult(
                    rule="괄호 짝",
                    severity="error",
                    message=f"닫는 괄호 '{ch}'에 대응하는 여는 괄호 '{expected_open}'가 없음",
                    paragraph=para_num,
                    text=_snippet(full_text, i, 1),
                ))

    for bracket, pos in stack:
        expected_close = _BRACKET_PAIRS[bracket]
        para_num = _pos_to_para(pos, para_offsets)
        results.append(CheckResult(
            rule="괄호 짝",
            severity="error",
            message=f"여는 괄호 '{bracket}'에 대응하는 닫는 괄호 '{expected_close}'가 없음",
            paragraph=para_num,
            text=_snippet(full_text, pos, 1),
        ))
    return results


# ──────────────────────────────────────────────
# 7. 법령 인용 형식 검사
# ──────────────────────────────────────────────
# 올바른 법령 인용 형식 예:
#   「민법」 제1조
#   「형사소송법」 제200조의2
#   같은 법 제10조제1항
_LAW_CITE_PATTERNS = [
    # 법령명을 「」 없이 인용한 경우 (형법, 민법 등 모든 법령명)
    (
        re.compile(
            r"(?<![「])("
            r"[가-힣]+법|[가-힣]+령|[가-힣]+규칙|[가-힣]+조례"
            r")(?![」])\s*제\d+"
        ),
        "법령명은 「」(낫표)로 감싸야 합니다",
        "warning",
    ),
    # 「법령명」과 제N조 사이 공백 누락
    (
        re.compile(r"「[^」]+」제(\d+)"),
        "법령명 뒤에 공백을 추가하세요 (예: 「민법」 제1조)",
        "warning",
    ),
    # "동법", "동조" 사용 (지양 표현)
    (
        re.compile(r"동(법|조|항|호)"),
        "'동법/동조/동항/동호' 대신 '같은 법/같은 조/같은 항/같은 호'로 작성 권장",
        "warning",
    ),
    # 조와 항 사이 잘못된 공백 (제1조 제1항 → 제1조제1항)
    (
        re.compile(r"제(\d+)조\s+제(\d+)항"),
        "조와 항은 붙여 씁니다 (예: 제1조제1항)",
        "warning",
    ),
]


def check_legal_citation(paragraphs: list[str]) -> list[CheckResult]:
    """법령 인용 형식을 검사한다."""
    results = []
    for idx, para in enumerate(paragraphs, 1):
        for pattern, message, severity in _LAW_CITE_PATTERNS:
            for m in pattern.finditer(para):
                results.append(CheckResult(
                    rule="법령 인용",
                    severity=severity,
                    message=f"'{m.group()}' — {message}",
                    paragraph=idx,
                    text=_snippet(para, m.start(), len(m.group())),
                ))
    return results


# ──────────────────────────────────────────────
# 전체 검사 실행
# ──────────────────────────────────────────────
ALL_CHECKERS = [
    ("오탈자", check_typos),
    ("띄어쓰기 일관성", check_spacing_consistency),
    ("조항 형식", check_article_format),
    ("숫자 표기", check_number_format),
    ("날짜 형식", check_date_format),
]


def run_all_checks(paragraphs: list[str],
                   enabled: list[str] | None = None,
                   para_infos: list[ParagraphInfo] | None = None,
                   ) -> list[CheckResult]:
    """모든 검사를 실행하고 결과를 반환한다.

    Args:
        paragraphs: 검사할 문단 리스트
        enabled: 실행할 검사 이름 리스트. None이면 전체 실행.
        para_infos: ParagraphInfo 리스트 (페이지/줄 정보 포함). 있으면 결과에 반영.
    """
    results = []
    for name, checker in ALL_CHECKERS:
        if enabled and name not in enabled:
            continue
        results.extend(checker(paragraphs))

    # para_infos가 있으면 결과에 페이지/줄 정보를 채운다
    if para_infos:
        _fill_position_info(results, para_infos)

    return results


def _fill_position_info(results: list[CheckResult],
                        para_infos: list[ParagraphInfo]) -> None:
    """CheckResult에 페이지/줄 정보를 채운다."""
    for r in results:
        idx = r.paragraph - 1  # 0-based
        if 0 <= idx < len(para_infos):
            info = para_infos[idx]
            r.page = info.page
            r.line = info.line


# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────
def _snippet(text: str, pos: int, length: int, context: int = 20) -> str:
    """해당 위치 주변 텍스트를 잘라 반환한다."""
    start = max(0, pos - context)
    end = min(len(text), pos + length + context)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end] + suffix


def _pos_to_para(pos: int, para_offsets: list[int]) -> int:
    """전체 텍스트 내 위치를 문단 번호(1-based)로 변환한다."""
    for i in range(len(para_offsets) - 1, -1, -1):
        if pos >= para_offsets[i]:
            return i + 1
    return 1
