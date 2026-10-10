import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).with_name('connect-mac.sh')
class ConnectTests(unittest.TestCase):
    def run_case(self,status='device',ready='true',host='uno_q',mapping='',busy=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            adb=root/'adb';adb.write_text('''#!/bin/bash
case "$*" in
 *get-state*) echo "$STATE";;
 'forward --list') printf '%s\\n' "$MAPPING";;
 *forward*) if [ "$BUSY" = 1 ] && [[ "$*" == *"tcp:9883 tcp:9883"* ]]; then exit 1; fi; echo "$*" >> "$CALLS";;
 *) exit 9;;
esac
''');adb.chmod(0o755)
            curl=root/'curl';curl.write_text('''#!/bin/bash
printf '{"ready":%s,"execution":{"host":"%s"}}' "$READY" "$HOST"
''');curl.chmod(0o755)
            env=dict(os.environ,PATH=str(root)+':'+os.environ['PATH'],GENGUARD_ADB=str(adb),GENGUARD_NO_OPEN='1',STATE=status,READY=ready,HOST=host,MAPPING=mapping,BUSY='1' if busy else '0',CALLS=str(root/'calls'))
            result=subprocess.run(['/bin/bash',str(SCRIPT),'--once'],env=env,text=True,capture_output=True,timeout=5)
            return result,(root/'calls').read_text() if (root/'calls').exists() else ''
    def test_cold_boot_waits_without_starting_board(self):
        result,calls=self.run_case(ready='false');self.assertEqual(result.returncode,2);self.assertIn('부팅',result.stdout);self.assertNotIn('shell',calls)
    def test_disconnect_does_not_create_forward(self):
        result,calls=self.run_case(status='');self.assertEqual(result.returncode,2);self.assertFalse(calls)
    def test_reconnect_creates_scoped_tunnel(self):
        result,calls=self.run_case();self.assertEqual(result.returncode,0);self.assertIn('-s 233936836 forward --no-rebind tcp:9883 tcp:9883',calls)
    def test_existing_mapping_reused(self):
        result,calls=self.run_case(mapping='233936836 tcp:9885 tcp:9883');self.assertEqual(result.returncode,0);self.assertIn(':9885/',result.stdout);self.assertFalse(calls)
    def test_busy_port_uses_another_without_rebind(self):
        result,calls=self.run_case(busy=True);self.assertEqual(result.returncode,0);self.assertIn(':9884/',result.stdout);self.assertIn('--no-rebind',calls)
    def test_wrong_server_never_claims_connection(self):
        result,_=self.run_case(host='mac');self.assertEqual(result.returncode,2);self.assertNotIn('연결 완료:',result.stdout)
if __name__=='__main__':unittest.main()
