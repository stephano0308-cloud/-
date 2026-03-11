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
import sys

from hwpx_reader import read_hwpx_with_position, read_text_file_with_position, ParagraphInfo
from checkers import run_all_checks, ALL_CHECKERS, CheckResult


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
    parser.add_argument("file", help="검사할 파일 경로 (.hwpx 또는 .txt)")
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
    return parser.parse_args()


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
    """CheckResult의 위치를 '페이지 줄' 형식으로 포맷한다."""
    if r.page > 0 and r.line > 0:
        return f"{r.page}페이지 {r.line}줄"
    elif r.page > 0:
        return f"{r.page}페이지"
    else:
        return f"문단 {r.paragraph}"


def format_text_report(results: list[CheckResult], filepath: str,
                       summary_only: bool = False) -> str:
    """검사 결과를 읽기 좋은 텍스트 보고서로 포맷한다."""
    lines = []
    lines.append("=" * 60)
    lines.append("  HWPX 맞춤법·형식 검사 결과")
    lines.append(f"  파일: {filepath}")
    lines.append("=" * 60)

    if not results:
        lines.append("\n  검사 결과 오류가 발견되지 않았습니다.")
        lines.append("=" * 60)
        return "\n".join(lines)

    # 심각도별 카운트
    error_count = sum(1 for r in results if r.severity == "error")
    warning_count = sum(1 for r in results if r.severity == "warning")

    lines.append(f"\n  총 {len(results)}건 발견 (오류: {error_count}, 경고: {warning_count})")
    lines.append("")

    # 규칙별 그룹핑
    by_rule: dict[str, list[CheckResult]] = {}
    for r in results:
        by_rule.setdefault(r.rule, []).append(r)

    for rule_name, rule_results in by_rule.items():
        lines.append(f"── {rule_name} ({len(rule_results)}건) ──")
        if not summary_only:
            for r in rule_results:
                severity_mark = "[오류]" if r.severity == "error" else "[경고]"
                location = _format_location(r)
                lines.append(f"  {severity_mark} {location}: {r.message}")
                lines.append(f"         >> {r.text}")
        lines.append("")

    lines.append("=" * 60)
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
    if any(r.severity == "error" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
