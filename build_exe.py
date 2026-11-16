"""PyInstaller를 사용하여 hwpx-checker 실행 파일을 빌드한다.

사용법:
    pip install pyinstaller
    python build_exe.py
"""

import os
import subprocess
import sys


def main():
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--clean",
        "--name", "hwpx-checker",
        "--add-data", f"rules.json{os.pathsep}.",
        "--add-data", f"law_names.json{os.pathsep}.",
        "--hidden-import", "hwpx_parser",
        "--hidden-import", "locator",
        "--hidden-import", "checker",
        "--hidden-import", "reporter",
        "--hidden-import", "law_checker",
        "--hidden-import", "law_api",
        "main.py",
    ]
    print(f"빌드 명령: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print("\n빌드 완료! dist/hwpx-checker 파일을 확인하세요.")
        if sys.platform == "win32":
            print("Windows: dist\\hwpx-checker.exe")
    else:
        print("\n빌드 실패!", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
