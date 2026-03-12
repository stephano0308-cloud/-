"""HWPX 맞춤법·형식 검사기 — 메인 실행 파일

사용법:
    python main.py <파일경로.hwpx>            # HWPX 파일 검사
    python main.py <파일경로.txt>             # 텍스트 파일 검사
    python main.py <파일경로> --rules 오탈자 띄어쓰기  # 특정 규칙만 검사
    python main.py <파일경로> --json           # JSON 형식 출력
    python main.py <파일경로> --output report.txt  # 결과를 파일로 저장
"""

import argparse
import json
import os
import re
import sys

from hwpx_reader import read_hwpx_with_position, read_text_file_with_position, ParagraphInfo
from checkers import run_all_checks, ALL_CHECKERS, CheckResult


def _select_file_dialog() -> str | None:
    """파일 선택 대화상자를 열어 파일 경로를 반환한다."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()  # 메인 창 숨기기
        root.attributes("-topmost", True)  # 대화상자를 최상위로
        filepath = filedialog.askopenfilename(
            title="검사할 파일을 선택하세요",
            filetypes=[
                ("HWPX 파일", "*.hwpx"),
                ("텍스트 파일", "*.txt"),
                ("모든 파일", "*.*"),
            ],
        )
        root.destroy()
        return filepath if filepath else None
    except ImportError:
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HWPX 문서 맞춤법·형식 검사기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
검사 규칙:
  오탈자          흔한 한국어 오탈자 패턴 검사
  띄어쓰기 일관성  같은 단어의 띄어쓰기 불일치 검사
  조항 형식       제○조/제○항/제○호 표기 형식 검사
  날짜 형식       날짜 유효성 및 구분자 통일 검사
  괄호 짝         괄호/낫표 등 짝 맞춤 검사
  법령 인용       법령명 낫표, 조항 붙여쓰기 등 검사

예시:
  hwpx_checker 계약서.hwpx
  hwpx_checker 계약서.hwpx --rules 오탈자 "괄호 짝"
  hwpx_checker 계약서.hwpx --json --output result.json
        """,
    )
    parser.add_argument("file", nargs="?", default=None, help="검사할 파일 경로 (.hwpx 또는 .txt)")
    parser.add_argument(
        "--rules",
        nargs="+",
        metavar="RULE",
        help=f"검사할 규칙 이름 (기본: 전체). 선택 가능: {', '.join(n for n, _ in ALL_CHECKERS)}",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="결과를 JSON 형식으로 출력",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="결과를 파일로 저장",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="요약만 출력 (상세 내역 생략)",
    )
    args = parser.parse_args()

    # 파일 인자가 없으면 파일 선택 대화상자를 띄운다
    if args.file is None:
        selected = _select_file_dialog()
        if selected:
            args.file = selected
        else:
            print("파일이 선택되지 않았습니다.", file=sys.stderr)
            sys.exit(0)

    return args


def load_document(filepath: str) -> list[ParagraphInfo]:
    """파일 확장자에 따라 적절한 리더를 사용한다."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".hwpx":
        return read_hwpx_with_position(filepath)
    elif ext in (".txt", ".md", ".rst"):
        return read_text_file_with_position(filepath)
    else:
        print(f"[알림] 확장자 '{ext}'는 텍스트 파일로 처리합니다.", file=sys.stderr)
        return read_text_file_with_position(filepath)


def _format_location(r: CheckResult) -> str:
    """CheckResult의 위치를 포맷한다."""
    return f"{r.paragraph}문단"


def _extract_current_improved(r: CheckResult) -> tuple[str, str]:
    """CheckResult의 message에서 현행/개선 텍스트를 추출한다."""
    # "'현행' → '개선'" 패턴
    m = re.match(r"'(.+?)'\s*→\s*'(.+?)'", r.message)
    if m:
        return m.group(1), m.group(2)
    # "'현행' — 설명" 패턴
    m = re.match(r"'(.+?)'\s*—\s*(.+)", r.message)
    if m:
        return m.group(1), m.group(2)
    return r.message, ""


def _display_width(s: str) -> int:
    """문자열의 표시 너비를 계산한다 (한글=2, 영문/숫자=1)."""
    width = 0
    for ch in s:
        if '\uac00' <= ch <= '\ud7a3' or '\u4e00' <= ch <= '\u9fff' or ch in '「」『』【】〈〉《》':
            width += 2
        else:
            width += 1
    return width


def _pad(s: str, target_width: int) -> str:
    """문자열을 target_width만큼 공백으로 패딩한다."""
    current = _display_width(s)
    return s + " " * max(0, target_width - current)


def format_text_report(results: list[CheckResult], filepath: str,
                       summary_only: bool = False) -> str:
    """검사 결과를 표 형식 보고서로 포맷한다."""
    lines = []
    lines.append("=" * 80)
    lines.append("  HWPX 맞춤법·형식 검사 결과")
    lines.append(f"  파일: {filepath}")
    lines.append("=" * 80)

    if not results:
        lines.append("\n  검사 결과 오류가 발견되지 않았습니다.")
        lines.append("=" * 80)
        return "\n".join(lines)

    # 오류를 경고보다 먼저 정렬
    results = sorted(results, key=lambda r: (0 if r.severity == "error" else 1, r.paragraph))

    # 심각도별 카운트
    error_count = sum(1 for r in results if r.severity == "error")
    warning_count = sum(1 for r in results if r.severity == "warning")

    lines.append(f"\n  총 {len(results)}건 발견 (오류: {error_count}, 경고: {warning_count})")
    lines.append("")

    if summary_only:
        by_rule: dict[str, list[CheckResult]] = {}
        for r in results:
            by_rule.setdefault(r.rule, []).append(r)
        for rule_name, rule_results in by_rule.items():
            lines.append(f"  {rule_name}: {len(rule_results)}건")
        lines.append("=" * 80)
        return "\n".join(lines)

    # 심각도 마커
    _SEV_MARKER = {"error": "✗ 오류", "warning": "△ 경고"}

    # 표 데이터 준비 (심각도 컬럼 추가)
    rows: list[tuple[str, str, str, str, str, str]] = []
    result_contexts: list[str] = []  # 문맥 텍스트
    for i, r in enumerate(results, 1):
        no = str(i)
        severity = _SEV_MARKER.get(r.severity, r.severity)
        location = _format_location(r)
        rule = r.rule
        current, improved = _extract_current_improved(r)
        rows.append((no, severity, location, rule, current, improved))
        result_contexts.append(r.text)

    # 열 너비 계산
    headers = ("No", "심각도", "위치", "검사항목", "현행", "개선")
    col_widths = [_display_width(h) for h in headers]
    for row in rows:
        for j, cell in enumerate(row):
            col_widths[j] = max(col_widths[j], _display_width(cell))

    # 최소/최대 너비 조정
    col_widths[0] = max(col_widths[0], 4)   # No
    col_widths[1] = max(col_widths[1], 8)   # 심각도
    col_widths[2] = max(col_widths[2], 8)   # 위치
    col_widths[3] = max(col_widths[3], 10)  # 검사항목
    col_widths[4] = max(col_widths[4], 14)  # 현행
    col_widths[5] = max(col_widths[5], 14)  # 개선

    def format_row(cells: tuple[str, ...]) -> str:
        parts = []
        for j, cell in enumerate(cells):
            parts.append(_pad(cell, col_widths[j]))
        return "  " + " | ".join(parts)

    # 구분선
    sep = "  " + "-+-".join("-" * w for w in col_widths)
    # 굵은 구분선 (오류/경고 섹션 사이)
    thick_sep = "  " + "=+=" .join("=" * w for w in col_widths)

    # 헤더
    lines.append(format_row(headers))
    lines.append(thick_sep)

    # 데이터 행
    prev_severity = None
    for i, (row, ctx) in enumerate(zip(rows, result_contexts)):
        cur_severity = results[i].severity
        # 오류→경고 전환 시 굵은 구분선 삽입
        if prev_severity == "error" and cur_severity == "warning":
            lines.append(thick_sep)
        elif i > 0:
            lines.append(sep)

        lines.append(format_row(row))

        # 문맥 텍스트가 있으면 아래에 표시
        if ctx:
            ctx_label = f"     ↳ 문맥: {ctx}"
            lines.append(ctx_label)

        prev_severity = cur_severity

    lines.append(thick_sep)
    lines.append("=" * 80)
    return "\n".join(lines)


def format_json_report(results: list[CheckResult], filepath: str) -> str:
    """검사 결과를 JSON으로 포맷한다."""
    data = {
        "file": filepath,
        "total": len(results),
        "errors": sum(1 for r in results if r.severity == "error"),
        "warnings": sum(1 for r in results if r.severity == "warning"),
        "results": [
            {
                "rule": r.rule,
                "severity": r.severity,
                "message": r.message,
                "page": r.page,
                "line": r.line,
                "paragraph": r.paragraph,
                "context": r.text,
            }
            for r in results
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def main():
    args = parse_args()

    # 파일 읽기
    try:
        para_infos = load_document(args.file)
    except (FileNotFoundError, ValueError) as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)

    if not para_infos:
        print("문서에서 텍스트를 추출할 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    paragraphs = [info.text for info in para_infos]
    print(f"문서에서 {len(paragraphs)}개 문단을 추출했습니다.", file=sys.stderr)

    # 검사 실행
    results = run_all_checks(paragraphs, enabled=args.rules, para_infos=para_infos)

    # 결과 포맷
    if args.json:
        output = format_json_report(results, args.file)
    else:
        output = format_text_report(results, args.file, summary_only=args.summary)

    # 출력
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"결과가 '{args.output}'에 저장되었습니다.", file=sys.stderr)
    else:
        print(output)

    # 오류가 있으면 종료 코드 1
    exit_code = 1 if any(r.severity == "error" for r in results) else 0

    # exe로 직접 실행(더블클릭)한 경우 결과를 확인할 수 있도록 대기
    if getattr(sys, "frozen", False):
        input("\n종료하려면 Enter 키를 누르세요...")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
