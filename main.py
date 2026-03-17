"""HWPX 문서 일관성 검수 CLI.

사용법:
    python main.py check sample.hwpx
    python main.py check ./docs --recursive
    python main.py check sample.hwpx --json out.json --csv out.csv
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from checker import run_all_checks
from locator import enrich_segments
from hwpx_parser import parse_hwpx
from reporter import print_console_report, save_csv, save_json

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """로깅을 설정한다."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def collect_hwpx_files(path: str, recursive: bool = False) -> list[str]:
    """경로에서 .hwpx 파일 목록을 수집한다."""
    target = Path(path)
    files: list[str] = []

    if target.is_file():
        if target.suffix.lower() == ".hwpx":
            files.append(str(target.resolve()))
        else:
            logger.warning("HWPX 파일이 아닙니다: %s", path)
    elif target.is_dir():
        if recursive:
            for f in target.rglob("*.hwpx"):
                files.append(str(f.resolve()))
        else:
            for f in target.glob("*.hwpx"):
                files.append(str(f.resolve()))
        if not files:
            logger.warning("HWPX 파일을 찾을 수 없습니다: %s", path)
    else:
        logger.error("경로를 찾을 수 없습니다: %s", path)

    files.sort()
    return files


def process_single_file(
    file_path: str,
    include_table: bool = True,
    rules_path: str = None,
    min_confidence: float = 0.0,
    disable_consistency: bool = False,
    disable_structure: bool = False,
    disable_date: bool = False,
    disable_typo: bool = False,
) -> list:
    """단일 HWPX 파일을 처리한다."""
    logger.info("파일 처리 시작: %s", file_path)

    doc = parse_hwpx(file_path, include_table=include_table)
    if not doc.segments:
        logger.warning("텍스트를 추출할 수 없습니다: %s", file_path)
        return []

    enrich_segments(doc.segments)

    issues = run_all_checks(
        segments=doc.segments,
        rules_path=rules_path,
        min_confidence=min_confidence,
        disable_consistency=disable_consistency,
        disable_structure=disable_structure,
        disable_date=disable_date,
        disable_typo=disable_typo,
    )

    return issues


def cmd_check(args: argparse.Namespace) -> int:
    """check 서브커맨드를 실행한다."""
    files = collect_hwpx_files(args.path, recursive=args.recursive)
    if not files:
        logger.error("처리할 HWPX 파일이 없습니다.")
        return 1

    logger.info("총 %d개 파일 처리 예정", len(files))

    all_issues_by_file: dict[str, list] = {}
    all_issues_flat: list = []

    for file_path in files:
        try:
            issues = process_single_file(
                file_path=file_path,
                include_table=args.include_table,
                rules_path=args.rules,
                min_confidence=args.min_confidence,
                disable_consistency=args.disable_consistency_check,
                disable_structure=args.disable_structure_check,
                disable_date=args.disable_date_check,
                disable_typo=args.disable_typo_check,
            )
            all_issues_by_file[file_path] = issues
            all_issues_flat.extend(issues)
            print_console_report(issues, source_file=file_path)
        except Exception as e:
            logger.error("파일 처리 중 오류 발생 (%s): %s", file_path, e)
            if args.verbose:
                import traceback
                traceback.print_exc()

    # 결과 저장
    if args.json:
        save_json(all_issues_flat, args.json, source_file=", ".join(files))

    if args.csv:
        save_csv(all_issues_flat, args.csv, source_file=", ".join(files))

    # 요약
    total = len(all_issues_flat)
    if total > 0:
        print(f"\n전체 결과: {len(files)}개 파일에서 총 {total}개 이슈 발견")
    else:
        print(f"\n전체 결과: {len(files)}개 파일 검수 완료, 이슈 없음")

    return 0 if total == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자 파서를 구성한다."""
    parser = argparse.ArgumentParser(
        prog="hwpx-checker",
        description="HWPX 문서 일관성 검수 및 오탈자 검출 도구",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="상세 로그 출력",
    )

    subparsers = parser.add_subparsers(dest="command", help="사용 가능한 명령")

    # check 서브커맨드
    check_parser = subparsers.add_parser("check", help="HWPX 파일 검수 실행")
    check_parser.add_argument(
        "path",
        help="HWPX 파일 또는 디렉터리 경로",
    )
    check_parser.add_argument(
        "--recursive",
        action="store_true",
        default=False,
        help="하위 디렉터리까지 재귀 탐색",
    )
    check_parser.add_argument(
        "--include-table",
        action="store_true",
        default=False,
        help="표(table) 내부 텍스트도 검수 대상에 포함",
    )
    check_parser.add_argument(
        "--rules",
        type=str,
        default=None,
        help="규칙 파일(rules.json) 경로",
    )
    check_parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        help="최소 신뢰도 (0.0~1.0, 이 값 미만의 이슈는 제외)",
    )
    check_parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="결과를 JSON 파일로 저장 (경로 지정)",
    )
    check_parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="결과를 CSV 파일로 저장 (경로 지정)",
    )
    check_parser.add_argument(
        "--disable-consistency-check",
        action="store_true",
        default=False,
        help="표기 일관성 검사 비활성화",
    )
    check_parser.add_argument(
        "--disable-structure-check",
        action="store_true",
        default=False,
        help="목차 체계 검증 비활성화",
    )
    check_parser.add_argument(
        "--disable-date-check",
        action="store_true",
        default=False,
        help="날짜 형식 검사 비활성화",
    )
    check_parser.add_argument(
        "--disable-typo-check",
        action="store_true",
        default=False,
        help="오탈자 검출 비활성화",
    )

    return parser


def main() -> int:
    """메인 진입점."""
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(verbose=getattr(args, "verbose", False))

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "check":
        return cmd_check(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
