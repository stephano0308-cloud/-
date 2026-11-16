"""검수 결과 출력 모듈.

콘솔 출력, JSON 저장, CSV 저장을 지원한다.
"""

import csv
import json
import logging
import os
import sys
from typing import TextIO

from checker import Issue

logger = logging.getLogger(__name__)

# 콘솔 색상 코드
COLORS = {
    "red": "\033[91m",
    "yellow": "\033[93m",
    "green": "\033[92m",
    "cyan": "\033[96m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}

SEVERITY_COLORS = {
    "high": "red",
    "medium": "yellow",
    "low": "cyan",
}

SEVERITY_LABELS = {
    "high": "높음",
    "medium": "보통",
    "low": "낮음",
}

TYPE_LABELS = {
    "consistency": "표기 일관성",
    "structure": "목차 체계",
    "date_format": "날짜 형식",
    "typo": "오탈자 의심",
}


def _color(text: str, color_name: str, use_color: bool = True) -> str:
    """텍스트에 ANSI 색상을 적용한다."""
    if not use_color:
        return text
    code = COLORS.get(color_name, "")
    reset = COLORS["reset"]
    return f"{code}{text}{reset}"


def _format_issue_console(issue: Issue, index: int, use_color: bool = True) -> str:
    """이슈 하나를 콘솔 출력용 문자열로 포맷한다."""
    lines: list[str] = []
    sev_color = SEVERITY_COLORS.get(issue.severity, "dim")
    sev_label = SEVERITY_LABELS.get(issue.severity, issue.severity)
    type_label = TYPE_LABELS.get(issue.issue_type, issue.issue_type)

    header = f"[{index}] [{type_label}] [{sev_label}] (신뢰도: {issue.confidence:.0%})"
    lines.append(_color(header, sev_color, use_color))

    lines.append(f"  원문: {issue.original}")
    lines.append(f"  추천: {issue.suggestion}")
    lines.append(f"  사유: {issue.reason}")

    loc = issue.location
    if loc:
        hint = loc.get("location_hint", "")
        if hint:
            lines.append(f"  위치: {hint}")
        file_info = loc.get("file", "")
        if file_info:
            lines.append(f"  파일: {file_info}")
        ctx_before = loc.get("context_before", "")
        ctx_after = loc.get("context_after", "")
        if ctx_before:
            lines.append(f"  앞 문맥: ...{ctx_before}")
        if ctx_after:
            lines.append(f"  뒷 문맥: {ctx_after}...")

    return "\n".join(lines)


def print_console_report(
    issues: list[Issue],
    source_file: str = "",
    stream: TextIO = sys.stdout,
    use_color: bool = True,
) -> None:
    """콘솔에 검수 결과를 출력한다."""
    if use_color and not hasattr(stream, "isatty"):
        use_color = False
    elif use_color and hasattr(stream, "isatty") and not stream.isatty():
        use_color = False

    separator = "=" * 60
    print(file=stream)
    print(_color(separator, "bold", use_color), file=stream)
    title = f"HWPX 문서 검수 결과"
    if source_file:
        title += f" - {os.path.basename(source_file)}"
    print(_color(title, "bold", use_color), file=stream)
    print(_color(separator, "bold", use_color), file=stream)

    if not issues:
        print(_color("검수 결과 이슈가 없습니다.", "green", use_color), file=stream)
        print(file=stream)
        return

    # 유형별 통계
    type_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for issue in issues:
        type_counts[issue.issue_type] = type_counts.get(issue.issue_type, 0) + 1
        severity_counts[issue.severity] = severity_counts.get(issue.severity, 0) + 1

    print(f"\n총 {len(issues)}개 이슈 발견:", file=stream)
    for itype, count in sorted(type_counts.items()):
        label = TYPE_LABELS.get(itype, itype)
        print(f"  - {label}: {count}건", file=stream)
    for sev, count in sorted(severity_counts.items(), key=lambda x: ["high", "medium", "low"].index(x[0]) if x[0] in ["high", "medium", "low"] else 99):
        label = SEVERITY_LABELS.get(sev, sev)
        print(f"  - {label}: {count}건", file=stream)
    print(file=stream)

    # 심각도 순 정렬
    severity_order = {"high": 0, "medium": 1, "low": 2}
    sorted_issues = sorted(issues, key=lambda i: severity_order.get(i.severity, 99))

    for idx, issue in enumerate(sorted_issues, 1):
        print(_format_issue_console(issue, idx, use_color), file=stream)
        print(file=stream)

    print(_color(separator, "dim", use_color), file=stream)
    print(file=stream)


def save_json(issues: list[Issue], output_path: str, source_file: str = "") -> None:
    """검수 결과를 JSON 파일로 저장한다."""
    data = {
        "source": source_file,
        "total_issues": len(issues),
        "issues": [issue.to_dict() for issue in issues],
    }
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("JSON 저장 완료: %s", output_path)
    except Exception as e:
        logger.error("JSON 저장 실패 (%s): %s", output_path, e)


def save_csv(issues: list[Issue], output_path: str, source_file: str = "") -> None:
    """검수 결과를 CSV 파일로 저장한다."""
    fieldnames = [
        "source",
        "issue_type",
        "severity",
        "confidence",
        "original",
        "suggestion",
        "reason",
        "file",
        "section",
        "paragraph_id",
        "sentence_index",
        "text_offset_start",
        "text_offset_end",
        "page",
        "line",
        "location_hint",
        "context_before",
        "context_after",
    ]
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for issue in issues:
                row = {
                    "source": source_file,
                    "issue_type": issue.issue_type,
                    "severity": issue.severity,
                    "confidence": round(issue.confidence, 2),
                    "original": issue.original,
                    "suggestion": issue.suggestion,
                    "reason": issue.reason,
                }
                loc = issue.location
                row["file"] = loc.get("file", "")
                row["section"] = loc.get("section", "")
                row["paragraph_id"] = loc.get("paragraph_id", "")
                row["sentence_index"] = loc.get("sentence_index", "")
                row["text_offset_start"] = loc.get("text_offset_start", "")
                row["text_offset_end"] = loc.get("text_offset_end", "")
                row["page"] = loc.get("page", "")
                row["line"] = loc.get("line", "")
                row["location_hint"] = loc.get("location_hint", "")
                row["context_before"] = loc.get("context_before", "")
                row["context_after"] = loc.get("context_after", "")
                writer.writerow(row)
        logger.info("CSV 저장 완료: %s", output_path)
    except Exception as e:
        logger.error("CSV 저장 실패 (%s): %s", output_path, e)
