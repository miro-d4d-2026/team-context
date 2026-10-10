"""Report the machine actually running inference, rather than a UI mode label."""
from pathlib import Path
import os
import platform


def execution_info():
    system, machine = platform.system(), platform.machine()
    try:
        board = Path('/sys/firmware/devicetree/base/model').read_text().rstrip('\0\n')
    except (OSError, UnicodeError):
        board = ''
    uno_q = system == 'Linux' and machine == 'aarch64' and board == 'Arduino SA,Imola'
    return {'host': 'uno_q' if uno_q else 'mac' if system == 'Darwin' else 'api_host',
            'label': 'UNO Q 내부 분석' if uno_q else 'Mac에서 분석' if system == 'Darwin' else '서버에서 분석',
            'system': system, 'architecture': machine, 'board': board or None,
            'pid': os.getpid(), 'external_ai_required': False}
