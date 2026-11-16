"""검수 엔진 모듈.

4가지 검수 엔진을 포함:
1. consistency - 표기 일관성 검사
2. structure  - 목차 체계 검증
3. date       - 날짜 형식 검사
4. typo       - 오탈자 의심 검출
"""

import json
import logging
import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Optional

from hwpx_parser import TextSegment

logger = logging.getLogger(__name__)

# ── 사건번호 보호 패턴 ──
CASE_NUMBER_PATTERN = re.compile(r"\d{4}[가-힣]{1,3}\d+")


@dataclass
class Issue:
    """검수에서 발견된 이슈 하나."""

    issue_type: str  # consistency | structure | date_format | typo
    severity: str  # low | medium | high
    confidence: float
    original: str
    suggestion: str
    reason: str
    location: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "issue_type": self.issue_type,
            "severity": self.severity,
            "confidence": round(self.confidence, 2),
            "original": self.original,
            "suggestion": self.suggestion,
            "reason": self.reason,
            "location": self.location,
        }


def _make_location(seg: TextSegment) -> dict:
    """TextSegment에서 위치 정보 dict를 생성한다."""
    return {
        "file": seg.file,
        "section": seg.section,
        "paragraph_id": seg.paragraph_id,
        "sentence_index": seg.sentence_index,
        "text_offset_start": seg.text_offset_start,
        "text_offset_end": seg.text_offset_end,
        "page": seg.page,
        "line": seg.line,
        "location_hint": seg.location_hint,
        "context_before": seg.context_before,
        "context_after": seg.context_after,
    }


def _is_case_number(text: str) -> bool:
    """사건번호 패턴 여부를 판별한다."""
    return bool(CASE_NUMBER_PATTERN.fullmatch(text.strip()))


def _contains_case_number(text: str) -> list[tuple[int, int]]:
    """텍스트 내 사건번호 위치를 모두 반환한다."""
    return [(m.start(), m.end()) for m in CASE_NUMBER_PATTERN.finditer(text)]


def _in_case_number_range(pos: int, ranges: list[tuple[int, int]]) -> bool:
    """주어진 위치가 사건번호 범위 내에 있는지 확인한다."""
    for start, end in ranges:
        if start <= pos < end:
            return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 표기 일관성 검사 (Consistency Engine)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _normalize_for_comparison(text: str) -> str:
    """비교를 위해 공백을 제거한 정규화 문자열을 반환한다."""
    return re.sub(r"\s+", "", text)


def _load_rules(rules_path: Optional[str] = None) -> dict:
    """규칙 파일을 로드한다."""
    if rules_path and os.path.isfile(rules_path):
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("규칙 파일 로드 실패 (%s): %s", rules_path, e)
    return {}


def check_consistency(
    segments: list[TextSegment],
    rules_path: Optional[str] = None,
    min_confidence: float = 0.0,
) -> list[Issue]:
    """표기 일관성 검사를 수행한다.

    동일한 용어가 다른 표기로 사용된 경우를 검출한다.
    """
    issues: list[Issue] = []
    rules = _load_rules(rules_path)
    known_groups: list[list[str]] = rules.get("consistency_groups", [])

    # 1단계: 문서 전체에서 용어 수집
    term_occurrences: dict[str, list[tuple[str, TextSegment]]] = defaultdict(list)

    for seg in segments:
        text = seg.text
        case_ranges = _contains_case_number(text)

        # 한글 단어 토큰 추출 (2글자 이상)
        for m in re.finditer(r"[가-힣]{2,}", text):
            if _in_case_number_range(m.start(), case_ranges):
                continue
            word = m.group()
            normalized = _normalize_for_comparison(word)
            term_occurrences[normalized].append((word, seg))

    # 2단계: 규칙 기반 일관성 검사
    for group in known_groups:
        if len(group) < 2:
            continue
        normalized_group = {_normalize_for_comparison(v): v for v in group}
        found_variants: dict[str, list[tuple[str, TextSegment]]] = {}

        for norm_key, canonical in normalized_group.items():
            if norm_key in term_occurrences:
                for surface, seg in term_occurrences[norm_key]:
                    found_variants.setdefault(surface, []).append((surface, seg))

        if len(found_variants) > 1:
            # 가장 많이 등장한 표기를 대표로 선택
            counts = {v: len(occ) for v, occ in found_variants.items()}
            representative = max(counts, key=counts.get)

            for variant, occurrences in found_variants.items():
                if variant == representative:
                    continue
                for surface, seg in occurrences:
                    conf = min(1.0, counts[representative] / max(sum(counts.values()), 1))
                    if conf < min_confidence:
                        continue
                    issues.append(Issue(
                        issue_type="consistency",
                        severity="medium",
                        confidence=conf,
                        original=surface,
                        suggestion=f"'{representative}'(으)로 통일 권장 (문서 내 {counts[representative]}회 사용)",
                        reason=f"동일 용어의 다른 표기: '{variant}' vs '{representative}'",
                        location=_make_location(seg),
                    ))

    # 3단계: 자동 감지 - 공백만 다른 변형 찾기
    normalized_to_surfaces: dict[str, Counter] = defaultdict(Counter)
    normalized_to_segments: dict[str, dict[str, list[TextSegment]]] = defaultdict(lambda: defaultdict(list))

    for seg in segments:
        text = seg.text
        case_ranges = _contains_case_number(text)
        # 한글 단어/구 추출 (공백 포함 가능, 2~10글자)
        for m in re.finditer(r"[가-힣]+(?:\s[가-힣]+)*", text):
            phrase = m.group()
            if _in_case_number_range(m.start(), case_ranges):
                continue
            if len(phrase.replace(" ", "")) < 2:
                continue
            normalized = _normalize_for_comparison(phrase)
            if len(normalized) < 2 or len(normalized) > 10:
                continue
            normalized_to_surfaces[normalized][phrase] += 1
            normalized_to_segments[normalized][phrase].append(seg)

    for normalized, surface_counts in normalized_to_surfaces.items():
        if len(surface_counts) < 2:
            continue
        # 공백만 다른 변형인지 확인
        surfaces = list(surface_counts.keys())
        has_space_diff = False
        for i in range(len(surfaces)):
            for j in range(i + 1, len(surfaces)):
                if surfaces[i].replace(" ", "") == surfaces[j].replace(" ", ""):
                    has_space_diff = True
                    break
        if not has_space_diff:
            continue

        representative = surface_counts.most_common(1)[0][0]
        rep_count = surface_counts[representative]
        total = sum(surface_counts.values())

        for variant, count in surface_counts.items():
            if variant == representative:
                continue
            for seg in normalized_to_segments[normalized][variant]:
                conf = min(1.0, rep_count / max(total, 1))
                if conf < min_confidence:
                    continue
                issues.append(Issue(
                    issue_type="consistency",
                    severity="low",
                    confidence=conf,
                    original=variant,
                    suggestion=f"'{representative}'(으)로 통일 권장 (문서 내 {rep_count}회 사용)",
                    reason=f"동일 용어의 다른 표기: '{variant}'({count}회) vs '{representative}'({rep_count}회)",
                    location=_make_location(seg),
                ))

    # 4단계: 숫자/금액/단위 표기 일관성
    unit_patterns = [
        (r"(\d[\d,]*)\s*(원|건|명|개|호|회|일|년|월|%|퍼센트)", "숫자+단위"),
    ]
    unit_occurrences: dict[str, Counter] = defaultdict(Counter)
    unit_segments: dict[str, dict[str, list[TextSegment]]] = defaultdict(lambda: defaultdict(list))

    for seg in segments:
        text = seg.text
        case_ranges = _contains_case_number(text)
        for pattern, label in unit_patterns:
            for m in re.finditer(pattern, text):
                if _in_case_number_range(m.start(), case_ranges):
                    continue
                full = m.group()
                number_part = m.group(1)
                unit_part = m.group(2)
                key = (number_part.replace(",", ""), unit_part)
                unit_occurrences[str(key)][full] += 1
                unit_segments[str(key)][full].append(seg)

    for key, surface_counts in unit_occurrences.items():
        if len(surface_counts) < 2:
            continue
        representative = surface_counts.most_common(1)[0][0]
        rep_count = surface_counts[representative]
        total = sum(surface_counts.values())
        for variant, count in surface_counts.items():
            if variant == representative:
                continue
            for seg in unit_segments[key][variant]:
                conf = min(1.0, rep_count / max(total, 1))
                if conf < min_confidence:
                    continue
                issues.append(Issue(
                    issue_type="consistency",
                    severity="low",
                    confidence=conf,
                    original=variant,
                    suggestion=f"'{representative}'(으)로 통일 권장",
                    reason=f"숫자/단위 표기 불일치: '{variant}' vs '{representative}'",
                    location=_make_location(seg),
                ))

    return issues


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 목차 체계 검증 (Structure Engine)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 목차 계층 정의
HEADING_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    # (label, pattern, level)
    # Level 0: 1. 2. 3. ...
    ("숫자점", re.compile(r"^(\d+)\.\s"), 0),
    # Level 1: 가. 나. 다. ...
    ("한글점", re.compile(r"^([가-힣])\.\s"), 1),
    # Level 2: (1) (2) (3) ...
    ("괄호숫자", re.compile(r"^\((\d+)\)\s"), 2),
    # Level 3: (가) (나) (다) ...
    ("괄호한글", re.compile(r"^\(([가-힣])\)\s"), 3),
    # Level 4: 1) 2) 3) ...
    ("숫자괄호", re.compile(r"^(\d+)\)\s"), 4),
    # Level 5: 가) 나) 다) ...
    ("한글괄호", re.compile(r"^([가-힣])\)\s"), 5),
]

# 한글 순서
KOREAN_ORDER = list("가나다라마바사아자차카타파하")
KOREAN_ORDER_MAP = {c: i for i, c in enumerate(KOREAN_ORDER)}


def _get_heading_info(text: str) -> Optional[tuple[str, str, int, str]]:
    """텍스트가 목차 항목인지 판별하고, (label, marker, level, value)를 반환한다."""
    stripped = text.strip()
    for label, pattern, level in HEADING_PATTERNS:
        m = pattern.match(stripped)
        if m:
            return (label, m.group(), level, m.group(1))
    return None


def _expected_next(label: str, current_value: str) -> Optional[str]:
    """현재 값에서 다음에 올 것으로 기대되는 값을 반환한다."""
    if label in ("숫자점", "괄호숫자", "숫자괄호"):
        try:
            return str(int(current_value) + 1)
        except ValueError:
            return None
    if label in ("한글점", "괄호한글", "한글괄호"):
        idx = KOREAN_ORDER_MAP.get(current_value)
        if idx is not None and idx + 1 < len(KOREAN_ORDER):
            return KOREAN_ORDER[idx + 1]
    return None


def check_structure(
    segments: list[TextSegment],
    min_confidence: float = 0.0,
) -> list[Issue]:
    """목차 체계를 stack 기반으로 검증한다."""
    issues: list[Issue] = []

    # stack: list of (level, label, value, segment)
    stack: list[tuple[int, str, str, TextSegment]] = []
    # 각 레벨별 마지막으로 본 값 추적
    level_last_value: dict[int, tuple[str, str]] = {}  # level -> (label, value)

    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue

        info = _get_heading_info(text)
        if info is None:
            continue

        label, marker, level, value = info

        # ── 레벨 점프 검사 ──
        if stack:
            top_level = stack[-1][0]
            if level > top_level + 1:
                conf = 0.95
                if conf >= min_confidence:
                    issues.append(Issue(
                        issue_type="structure",
                        severity="high",
                        confidence=conf,
                        original=marker.strip(),
                        suggestion=f"레벨 {top_level}에서 레벨 {level}로 건너뜀 (중간 레벨 누락)",
                        reason=f"목차 레벨 점프: 직전 레벨 {top_level} → 현재 레벨 {level}",
                        location=_make_location(seg),
                    ))

        # ── 순서 오류 검사 ──
        if level in level_last_value:
            last_label, last_value = level_last_value[level]
            if last_label == label:
                expected = _expected_next(label, last_value)
                if expected is not None and value != expected:
                    # 새로운 시퀀스 시작(1 또는 가)인지 확인
                    is_new_sequence = (value == "1" and label in ("숫자점", "괄호숫자", "숫자괄호")) or \
                                     (value == "가" and label in ("한글점", "괄호한글", "한글괄호"))

                    if not is_new_sequence:
                        conf = 0.9
                        if conf >= min_confidence:
                            issues.append(Issue(
                                issue_type="structure",
                                severity="high",
                                confidence=conf,
                                original=marker.strip(),
                                suggestion=f"'{expected}'이(가) 와야 하지만 '{value}'이(가) 나옴",
                                reason=f"목차 순서 오류: '{last_value}' 다음에 '{value}' (기대값: '{expected}')",
                                location=_make_location(seg),
                            ))
                    else:
                        # 새 시퀀스 시작 시 상위 레벨 스택 확인
                        while stack and stack[-1][0] >= level:
                            stack.pop()
                        # 하위 레벨 값 초기화
                        keys_to_remove = [k for k in level_last_value if k > level]
                        for k in keys_to_remove:
                            del level_last_value[k]

        # ── 체계 혼용 검사 ──
        if level in level_last_value:
            last_label, _ = level_last_value[level]
            if last_label != label:
                # 같은 레벨인데 다른 체계를 사용
                is_reset = (value == "1" and label in ("숫자점", "괄호숫자", "숫자괄호")) or \
                           (value == "가" and label in ("한글점", "괄호한글", "한글괄호"))
                if not is_reset:
                    conf = 0.85
                    if conf >= min_confidence:
                        issues.append(Issue(
                            issue_type="structure",
                            severity="medium",
                            confidence=conf,
                            original=marker.strip(),
                            suggestion=f"레벨 {level}에서 '{last_label}' 체계와 '{label}' 체계가 혼용됨",
                            reason=f"목차 체계 혼용: 같은 레벨에서 다른 번호 체계 사용",
                            location=_make_location(seg),
                        ))

        # 스택 갱신
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, label, value, seg))
        level_last_value[level] = (label, value)

        # 하위 레벨 값 초기화 (새 상위 항목 진입 시)
        keys_to_remove = [k for k in level_last_value if k > level]
        for k in keys_to_remove:
            del level_last_value[k]

    return issues


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 날짜 형식 검사 (Date Engine)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 정상 날짜 패턴: YYYY.M.D. (앞에 0이 없고, 마지막에 마침표)
VALID_DATE_PATTERN = re.compile(
    r"(?<!\d)(\d{4})\.(1[0-2]|[1-9])\.(3[01]|[12]\d|[1-9])\.(?!\d)"
)

# 오류 날짜 패턴들
ERROR_DATE_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # YYYY-MM-DD 또는 YYYY-M-D
    (
        re.compile(r"(?<!\d)(\d{4})-(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])(?!\d)"),
        "하이픈 구분자 사용",
        "YYYY.M.D. 형식으로 변경",
    ),
    # YYYY/M/D 또는 YYYY/MM/DD
    (
        re.compile(r"(?<!\d)(\d{4})/(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])(?!\d)"),
        "슬래시 구분자 사용",
        "YYYY.M.D. 형식으로 변경",
    ),
    # YYYY.MM.DD (앞에 0 포함) - 마침표 있든 없든
    (
        re.compile(r"(?<!\d)(\d{4})\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\.?(?!\d)"),
        "월/일에 앞자리 0 포함",
        "앞자리 0 제거 (YYYY.M.D. 형식)",
    ),
    # YYYY.M.D (마침표 없음) - 정상 패턴에 매칭되지 않는 것만
    (
        re.compile(r"(?<!\d)(\d{4})\.(1[0-2]|[1-9])\.(3[01]|[12]\d|[1-9])(?!\.)(?!\d)"),
        "마지막 마침표 누락",
        "마지막에 마침표 추가 (YYYY.M.D.)",
    ),
    # YYYY년 M월 D일
    (
        re.compile(r"(?<!\d)(\d{4})년\s*(0?[1-9]|1[0-2])월\s*(0?[1-9]|[12]\d|3[01])일"),
        "'년월일' 형식 사용",
        "YYYY.M.D. 형식으로 변경",
    ),
]


def _make_correct_date(year: str, month: str, day: str) -> str:
    """올바른 날짜 형식으로 변환한다."""
    m = str(int(month))
    d = str(int(day))
    return f"{year}.{m}.{d}."


def check_dates(
    segments: list[TextSegment],
    min_confidence: float = 0.0,
) -> list[Issue]:
    """날짜 형식을 검사한다. YYYY.M.D. 형식만 정상으로 인정한다."""
    issues: list[Issue] = []

    for seg in segments:
        text = seg.text
        case_ranges = _contains_case_number(text)

        # 정상 날짜 위치를 먼저 수집
        valid_positions: set[tuple[int, int]] = set()
        for m in VALID_DATE_PATTERN.finditer(text):
            valid_positions.add((m.start(), m.end()))

        for pattern, reason, suggestion_template in ERROR_DATE_PATTERNS:
            for m in pattern.finditer(text):
                # 사건번호 범위 내이면 스킵
                if _in_case_number_range(m.start(), case_ranges):
                    continue

                # 이미 정상 날짜로 매칭된 위치이면 스킵
                is_valid = False
                for vs, ve in valid_positions:
                    if vs <= m.start() and m.end() <= ve:
                        is_valid = True
                        break
                if is_valid:
                    continue

                original = m.group()
                year = m.group(1)
                month = m.group(2)
                day = m.group(3)
                corrected = _make_correct_date(year, month, day)

                # 이미 올바른 형식이면 스킵
                if original == corrected:
                    continue
                # 원본이 올바른 패턴에 매칭되면 스킵
                if VALID_DATE_PATTERN.fullmatch(original):
                    continue

                conf = 0.95
                if conf < min_confidence:
                    continue

                issues.append(Issue(
                    issue_type="date_format",
                    severity="high",
                    confidence=conf,
                    original=original,
                    suggestion=f"{corrected} ({suggestion_template})",
                    reason=f"날짜 형식 오류: {reason}",
                    location=_make_location(seg),
                ))

    return issues


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 오탈자 의심 검출 (Typo Engine)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 반복 입력 패턴 (연속 2음절 이상 반복)
REPEATED_SYLLABLE_PATTERN = re.compile(r"([가-힣]{1,3})\1")

# 혼동 용어 쌍 (원래 → 올바른)
CONFUSION_PAIRS: list[tuple[str, str, str]] = [
    ("됬", "됐", "혼동 용어: '됬' → '됐'"),
    ("돼지", "돼지", ""),  # 정상 - 제외용
    ("안됀", "안 된", "혼동 용어: '안됀' → '안 된'"),
    ("웬지", "왠지", "혼동 용어: '웬지' → '왠지'"),
    ("왠만", "웬만", "혼동 용어: '왠만' → '웬만'"),
    ("몇일", "며칠", "혼동 용어: '몇일' → '며칠'"),
    ("희안", "희한", "혼동 용어: '희안' → '희한'"),
    ("어의없", "어이없", "혼동 용어: '어의없' → '어이없'"),
    ("역활", "역할", "혼동 용어: '역활' → '역할'"),
    ("갈켜", "가르쳐", "혼동 용어: '갈켜' → '가르쳐'"),
    ("문안하", "문안하", ""),
    ("금액액", "금액", "반복 입력 의심"),
    ("세액액", "세액", "반복 입력 의심"),
    ("처분분", "처분", "반복 입력 의심"),
    ("결정정", "결정", "반복 입력 의심"),
]

# 실질적인 혼동 쌍만 필터
ACTIVE_CONFUSION_PAIRS = [(wrong, right, reason) for wrong, right, reason in CONFUSION_PAIRS if reason]


def _load_typo_rules(rules_path: Optional[str] = None) -> list[tuple[str, str, str]]:
    """규칙 파일에서 추가 오탈자 규칙을 로드한다."""
    extra: list[tuple[str, str, str]] = []
    if not rules_path or not os.path.isfile(rules_path):
        return extra
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data.get("typo_rules", []):
            wrong = item.get("wrong", "")
            right = item.get("right", "")
            reason = item.get("reason", f"규칙 기반: '{wrong}' → '{right}'")
            if wrong and right:
                extra.append((wrong, right, reason))
    except Exception as e:
        logger.warning("오탈자 규칙 로드 실패: %s", e)
    return extra


def check_typos(
    segments: list[TextSegment],
    rules_path: Optional[str] = None,
    min_confidence: float = 0.0,
) -> list[Issue]:
    """오탈자 의심 검출. 띄어쓰기 관련은 절대 검출하지 않는다."""
    issues: list[Issue] = []
    extra_rules = _load_typo_rules(rules_path)
    # 중복 제거: 내장 규칙과 외부 규칙에서 wrong이 같은 것은 내장 규칙 우선
    builtin_wrongs = {wrong for wrong, _, _ in ACTIVE_CONFUSION_PAIRS}
    deduped_extra = [(w, r, reason) for w, r, reason in extra_rules if w not in builtin_wrongs and reason]
    all_pairs = ACTIVE_CONFUSION_PAIRS + deduped_extra

    for seg in segments:
        text = seg.text

        # ── 혼동 용어 검출 ──
        for wrong, right, reason in all_pairs:
            if wrong in text:
                conf = 0.85
                if conf < min_confidence:
                    continue
                issues.append(Issue(
                    issue_type="typo",
                    severity="medium",
                    confidence=conf,
                    original=wrong,
                    suggestion=f"'{right}' ({reason})",
                    reason=reason,
                    location=_make_location(seg),
                ))

    return issues


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 통합 검수 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def run_all_checks(
    segments: list[TextSegment],
    rules_path: Optional[str] = None,
    min_confidence: float = 0.0,
    disable_consistency: bool = False,
    disable_structure: bool = True,
    disable_date: bool = False,
    disable_typo: bool = False,
    law_dict_path: Optional[str] = None,
    disable_law: bool = False,
) -> list[Issue]:
    """활성화된 모든 검수를 실행하고 이슈 리스트를 반환한다."""
    all_issues: list[Issue] = []

    law_dict = None
    if not disable_law and law_dict_path:
        from law_checker import load_law_dict

        law_dict = load_law_dict(law_dict_path)
        if not len(law_dict):
            law_dict = None

    if not disable_consistency:
        logger.info("표기 일관성 검사 실행 중...")
        consistency_issues = check_consistency(segments, rules_path, min_confidence)
        if law_dict is not None:
            # 법령명은 다수결이 아니라 공식 법령명 사전을 기준으로 판정한다
            from law_checker import mentions_law

            consistency_issues = [
                i for i in consistency_issues if not mentions_law(i.original, law_dict)
            ]
        all_issues.extend(consistency_issues)

    if law_dict is not None:
        from law_checker import check_law_names

        logger.info("법령명 검사 실행 중... (사전 %d건)", len(law_dict))
        all_issues.extend(check_law_names(segments, law_dict, min_confidence))

    if not disable_structure:
        logger.info("목차 체계 검증 실행 중...")
        all_issues.extend(check_structure(segments, min_confidence))

    if not disable_date:
        logger.info("날짜 형식 검사 실행 중...")
        all_issues.extend(check_dates(segments, min_confidence))

    if not disable_typo:
        logger.info("오탈자 검출 실행 중...")
        all_issues.extend(check_typos(segments, rules_path, min_confidence))

    logger.info("총 %d개 이슈 발견", len(all_issues))
    return all_issues
