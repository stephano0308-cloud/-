"""법령명 검수 모듈.

국가법령정보센터(law.go.kr)에서 수집한 공식 법령명 사전(law_names.json)을
기준으로 다음을 검출한다.

1. 법령명 공식 표기 불일치 (예: '국세 기본법', '상속세및증여세법', '소득세법시행령')
2. 법령명 오탈자 의심 (예: '조세특레제한법', '국세기븐법')

자동 수정은 하지 않고 공식 표기를 추천만 한다.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from checker import Issue, _make_location
from hwpx_parser import TextSegment

logger = logging.getLogger(__name__)

# 가운뎃점 변형은 모두 같은 문자로 취급한다 (표기 차이로 보지 않음)
_MIDDLE_DOTS = "·・‧･∙•"
_OFFICIAL_DOT = "ㆍ"

# 법령명 뒤에 바로 붙어도 되는 조사 (띄어 쓴 법령명 오검출 방지용)
_PARTICLES = (
    "으로", "에서", "에는", "에도", "에의", "과의", "와의", "상의", "이나", "이라",
    "은", "는", "이", "가", "을", "를", "의", "에", "과", "와", "상", "도", "로", "만",
)

# 사전에 없는 법령에도 적용하는 일반 규칙: '○○법시행령' → '○○법 시행령'
_ATTACHED_DECREE = re.compile(r"([가-힣ㆍ]{2,}(?:법|법률))(시행령|시행규칙)")

_MIN_FUZZY_LEN = 5


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def _norm_char(ch: str) -> str:
    return _OFFICIAL_DOT if ch in _MIDDLE_DOTS else ch


def _normalize(text: str) -> str:
    """공백 제거 + 가운뎃점 통일."""
    return "".join(_norm_char(c) for c in text if not c.isspace())


def _collapse(text: str) -> str:
    """공백 연속을 한 칸으로 줄이고 가운뎃점을 통일한다."""
    return re.sub(r"\s+", " ", "".join(_norm_char(c) for c in text)).strip()


@dataclass
class LawDict:
    """공식 법령명 사전."""

    # 정규화 키 → 공식 표기
    names: dict[str, str] = field(default_factory=dict)
    # 정규화된 약칭 집합
    abbrs: set[str] = field(default_factory=set)
    # 과거 법령명 등 오탈자 검사에서 제외할 이름 (정규화)
    exclusions: set[str] = field(default_factory=set)
    # 첫 글자 → 정규화 키 목록 (긴 것부터)
    by_first: dict[str, list[str]] = field(default_factory=dict)

    def build_index(self) -> None:
        self.by_first = {}
        for key in self.names:
            self.by_first.setdefault(key[0], []).append(key)
        for keys in self.by_first.values():
            keys.sort(key=len, reverse=True)

    def __len__(self) -> int:
        return len(self.names)


def load_law_dict(path: Optional[str]) -> LawDict:
    """law_names.json을 읽어 LawDict를 만든다. 실패하면 빈 사전을 반환한다."""
    law_dict = LawDict()
    if not path or not os.path.isfile(path):
        return law_dict
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("법령명 사전 로드 실패 (%s): %s", path, e)
        return law_dict

    for item in data.get("laws", []):
        name = _collapse(str(item.get("name", "")))
        if len(_normalize(name)) < 3:
            continue
        law_dict.names[_normalize(name)] = name
        abbr = _normalize(str(item.get("abbr", "") or ""))
        if abbr:
            law_dict.abbrs.add(abbr)
    for name in data.get("exclusions", []):
        key = _normalize(str(name))
        if key:
            law_dict.exclusions.add(key)
    law_dict.build_index()
    logger.info("법령명 사전 로드: %d건 (%s)", len(law_dict), path)
    return law_dict


def _space_offsets(text: str) -> set[int]:
    """공백이 '정규화 기준 몇 번째 글자 뒤'에 있는지의 집합을 반환한다."""
    offsets: set[int] = set()
    count = 0
    for ch in text:
        if ch.isspace():
            if count:
                offsets.add(count)
        else:
            count += 1
    return offsets


def _is_distance_one(a: str, b: str) -> bool:
    """두 문자열의 편집 거리가 정확히 1(치환·삽입·삭제·인접 전치)인지 확인한다."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (
            len(diff) == 2
            and diff[1] == diff[0] + 1
            and a[diff[0]] == b[diff[1]]
            and a[diff[1]] == b[diff[0]]
        )
    if abs(la - lb) != 1:
        return False
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def _starts_with_particle(rest: str) -> bool:
    if not rest or not _is_hangul(rest[0]):
        return True
    for p in _PARTICLES:
        if rest.startswith(p):
            after = rest[len(p):]
            if not after or not _is_hangul(after[0]):
                return True
    return False


def _scan_segment(text: str, law_dict: LawDict) -> list[tuple[str, str, str, int]]:
    """세그먼트 하나에서 (종류, 원문, 추천, 원문 시작 위치) 목록을 찾는다.

    종류: 'notation' (공식 표기 불일치) | 'typo' (오탈자 의심)
    """
    found: list[tuple[str, str, str, int]] = []

    norm_chars: list[str] = []
    orig_idx: list[int] = []
    for i, ch in enumerate(text):
        if not ch.isspace():
            norm_chars.append(_norm_char(ch))
            orig_idx.append(i)
    norm = "".join(norm_chars)
    n = len(norm)

    def orig_span(start: int, length: int) -> tuple[int, int]:
        return orig_idx[start], orig_idx[start + length - 1] + 1

    covered_until = 0  # 이미 법령명으로 판정한 원문 구간의 끝
    for s in range(n):
        o_start = orig_idx[s]
        if o_start < covered_until:
            continue
        if o_start > 0 and _is_hangul(text[o_start - 1]):
            continue  # 단어 중간에서는 시작하지 않는다
        candidates = law_dict.by_first.get(norm[s])
        if not candidates:
            continue

        # ── 1) 공식 법령명과 (공백 무시) 일치하는 가장 긴 이름 ──
        matched = False
        for key in candidates:
            if not norm.startswith(key, s):
                continue
            a, b = orig_span(s, len(key))
            original = _collapse(text[a:b])
            official = law_dict.names[key]
            if original == official:
                matched = True
                covered_until = b
                break
            extra_space = _space_offsets(original) - _space_offsets(official)
            if extra_space and not _starts_with_particle(text[b:b + 4]):
                # '소득세 법인세'처럼 다른 단어와 우연히 이어진 경우
                continue
            found.append(("notation", text[a:b], official, a))
            matched = True
            covered_until = b
            break
        if matched:
            continue

        # ── 2) 약칭·제외 목록이면 건너뛴다 ──
        if any(norm.startswith(x, s) for x in law_dict.abbrs) or any(
            norm.startswith(x, s) for x in law_dict.exclusions
        ):
            continue

        # ── 3) 한 글자만 다른 법령명 (오탈자 의심) ──
        for key in candidates:
            klen = len(key)
            if klen < _MIN_FUZZY_LEN:
                continue
            hit = None
            for clen in (klen, klen + 1, klen - 1):
                if s + clen > n:
                    continue
                cand = norm[s:s + clen]
                if cand[-1] != key[-1] or not all(_is_hangul(c) or c == _OFFICIAL_DOT for c in cand):
                    continue
                if _is_distance_one(cand, key):
                    hit = clen
                    break
            if hit is None:
                continue
            a, b = orig_span(s, hit)
            found.append(("typo", text[a:b], law_dict.names[key], a))
            covered_until = b
            break

    # ── 4) 일반 규칙: '○○법시행령' 붙여 쓰기 ──
    for m in _ATTACHED_DECREE.finditer(text):
        if any(a <= m.start() < a + len(orig) for _, orig, _, a in found):
            continue
        if m.start() > 0 and _is_hangul(text[m.start() - 1]):
            continue
        found.append(("notation", m.group(), f"{m.group(1)} {m.group(2)}", m.start()))

    return found


def check_law_names(
    segments: list[TextSegment],
    law_dict: LawDict,
    min_confidence: float = 0.0,
) -> list[Issue]:
    """법령명 공식 표기 불일치와 법령명 오탈자 의심을 검출한다."""
    issues: list[Issue] = []
    for seg in segments:
        if not seg.text or not seg.text.strip():
            continue
        for kind, original, official, _pos in _scan_segment(seg.text, law_dict):
            if kind == "notation":
                issue = Issue(
                    issue_type="consistency",
                    severity="medium",
                    confidence=0.9,
                    original=original,
                    suggestion=f"'{official}' (국가법령정보센터 공식 법령명)",
                    reason=f"법령명 공식 표기 불일치: '{original}' → '{official}'",
                    location=_make_location(seg),
                )
            else:
                issue = Issue(
                    issue_type="typo",
                    severity="medium",
                    confidence=0.6,
                    original=original,
                    suggestion=f"'{official}' (공식 법령명과 한 글자 차이)",
                    reason=f"법령명 오탈자 의심: '{original}' ↔ '{official}'",
                    location=_make_location(seg),
                )
            if issue.confidence >= min_confidence:
                issues.append(issue)
    return issues


def mentions_law(text: str, law_dict: LawDict) -> bool:
    """주어진 문자열에 사전의 법령명이 (공백 무시하고) 들어 있는지 확인한다."""
    key = _normalize(text)
    return any(name in key for name in law_dict.names)
