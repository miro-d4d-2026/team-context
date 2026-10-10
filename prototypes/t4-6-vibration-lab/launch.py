"""Start the already-installed UNO Q receiver and this local viewer."""
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADB = ROOT / '.tools/platform-tools/adb'
DEVICE = '233936836'
REMOTE = '/home/arduino/t4-6-vibration-lab'


def prepare_receiver():
    if not ADB.exists():
        raise RuntimeError('USB 연결 도구가 없습니다. README의 설치 상태를 확인하세요.')
    # Match this exact executable, never stop or overwrite other board programs.
    process = f'{REMOTE}/.venv/bin/python {REMOTE}/gateway/unoq_qwiic_gateway.py'
    command = (f"test -f {REMOTE}/gateway/unoq_qwiic_gateway.py && "
               f"(pgrep -f '^{process}$' >/dev/null || "
               f"(nohup {process} >{REMOTE}/gateway.log 2>&1 </dev/null &))")
    subprocess.run([str(ADB), '-s', DEVICE, 'shell', command], check=True, timeout=10)
    subprocess.run([str(ADB), '-s', DEVICE, 'forward', 'tcp:9877', 'tcp:9877'],
                   check=True, timeout=10)


def main():
    try:
        prepare_receiver()
        print('UNO Q 수신기를 준비했습니다.', flush=True)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(f'UNO Q 준비 실패: {exc}\n웹 화면은 열립니다. USB 연결과 설치 상태를 확인하세요.', flush=True)
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=1):
            print('이미 실행 중입니다: http://127.0.0.1:8765')
            return
    except OSError:
        pass
    subprocess.run([sys.executable, str(ROOT/'server.py')], check=True)


if __name__ == '__main__':
    main()
