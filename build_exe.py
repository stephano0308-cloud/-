"""PyInstaller를 사용하여 hwpx_checker.exe를 빌드하는 스크립트.

사용법:
    pip install pyinstaller
    python build_exe.py

또는 직접:
    pyinstaller --onefile --name hwpx_checker main.py
"""

import subprocess
import sys


def build():
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "hwpx_checker",
        "--console",
        "--clean",
        "main.py",
    ]

    print(f"빌드 명령: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=".")
    if result.returncode == 0:
        print("\n빌드 완료! dist/hwpx_checker.exe 파일이 생성되었습니다.")
    else:
        print("\n빌드 실패.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    build()
