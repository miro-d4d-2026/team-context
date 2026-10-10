#!/usr/bin/env python3
"""Loopback-only NU-40 serial viewer. No device-control or simulated readings."""
import argparse
import csv
import io
import json
import math
import re
import socket
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parent
FIELDS = ['id', 'seq', 't_us', 'elapsed_s', 'host_time', 'ax_g', 'ay_g', 'az_g',
          'gx_dps', 'gy_dps', 'gz_dps']
AXES = FIELDS[5:]
ID_RE = re.compile(r'^\d{8}T\d{6}-[a-f0-9]{8}$')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def ports():
    found = []
    for p in list_ports.comports():
        if p.vid is None or not p.device.startswith('/dev/cu.'):
            continue
        hint = 'USB 직렬 장치'
        recommended = p.vid == 0x239A and p.pid in (0x80AC, 0x8029, 0x0029)
        if recommended:
            hint = 'NU-40 계열 (nRF52840)'
        elif p.vid == 0x239A and p.pid == 0x00DA:
            hint = 'nRF52840 부트로더 · 펌웨어 업로드 필요'
        elif p.vid == 0x2341 and p.pid == 0x0078:
            hint = 'Arduino UNO Q · 이번 센서 연결 대상 아님'
        found.append(dict(device=p.device, description=p.description,
                          board_hint=hint, recommended=recommended))
    return sorted(found, key=lambda p: (not p['recommended'], p['device']))


class Lab:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.history = deque(maxlen=5000)
        self._id = 0
        self._serial = None
        self._socket = None
        self._stop = threading.Event()
        self._thread = None
        self.connected = False
        self.port = None
        self.source = None
        self.gateway = {}
        self.meta = {}
        self.error = None
        self.recording = None
        self._file = None
        self._writer = None
        self._reset_stream()

    def _reset_stream(self):
        self.history.clear()
        self.count = self.dropped = self.invalid = 0
        self._last_seq = self._last_t = None
        self._elapsed_us = 0
        self._last_rx = None
        self._first_rx = None
        self.meta = {}
        self.gateway = {}
        self.error = None

    def connect(self, port=None, source='serial'):
        if source in ('unoq_qwiic', 'unoq_ble'):
            self.disconnect()
            try:
                connection = socket.create_connection(('127.0.0.1', 9877), timeout=3)
            except OSError as exc:
                raise ValueError('UNO Q 수신기에 연결할 수 없습니다. UNO Q USB 연결과 수신기 실행을 확인하세요.') from exc
            connection.settimeout(0.25)
            with self.lock:
                self._reset_stream()
                self.source = source
                self.port = 'UNO Q · QWIIC → USB' if source == 'unoq_qwiic' else 'UNO Q · USB → BLE → NU-40'
                self.connected = True
                self._socket = connection
                self._stop = threading.Event()
                self._thread = threading.Thread(target=self._read_gateway,
                    args=(connection, self._stop), daemon=True)
                self._thread.start()
            return
        if source != 'serial':
            raise ValueError('지원하지 않는 입력 방식입니다.')
        if port not in {p['device'] for p in ports()}:
            raise ValueError('현재 연결된 USB 포트에서 선택해 주세요.')
        self.disconnect()
        device = serial.Serial(port, 115200, timeout=0.25, write_timeout=0.25,
                               exclusive=True)
        device.reset_input_buffer()
        with self.lock:
            self._reset_stream()
            self.source = 'serial'
            self.port, self.connected = port, True
            self._serial = device
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._read, args=(device, self._stop),
                                            daemon=True)
            self._thread.start()

    def disconnect(self):
        self._stop.set()
        reader = self._thread
        if reader and reader is not threading.current_thread():
            reader.join(timeout=1)
        with self.lock:
            if self._serial:
                self._serial.close()
            self._serial = None
            if self._socket:
                self._socket.close()
            self._socket = None
            self.connected = False
            if self.recording:
                self._finish_recording('disconnected')

    def _read(self, device, stop):
        try:
            while not stop.is_set():
                line = device.readline(1024)
                if line:
                    self.ingest(line)
                if self.recording and self._last_rx and time.monotonic() - self._last_rx > 3:
                    with self.lock:
                        self._finish_recording('sensor_stale')
        except (serial.SerialException, OSError) as exc:
            with self.lock:
                self.error = 'USB 연결이 끊겼습니다. 포트를 새로고침하고 다시 연결하세요.'
                self.connected = False
                self._finish_recording('serial_error')
            print(f'Serial stopped: {exc}', flush=True)
        finally:
            device.close()
            with self.lock:
                self.connected = False

    def _read_gateway(self, connection, stop):
        pending = b''
        try:
            while not stop.is_set():
                try:
                    chunk = connection.recv(8192)
                except socket.timeout:
                    chunk = None
                if chunk == b'':
                    raise OSError('UNO Q gateway closed the stream')
                if chunk:
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        if len(line) <= 4096:
                            self.ingest(line)
                        else:
                            with self.lock:
                                self.invalid += 1
                    if len(pending) > 4096:
                        raise OSError('Invalid gateway packet length')
                with self.lock:
                    if self.recording and self._last_rx and time.monotonic() - self._last_rx > 3:
                        self._finish_recording('sensor_stale')
        except OSError as exc:
            if not stop.is_set():
                with self.lock:
                    self.error = 'UNO Q 수신기 연결이 끊겼습니다. USB와 수신기를 확인한 뒤 다시 연결하세요.'
                    self._finish_recording('gateway_disconnected')
                print(f'Gateway stopped: {exc}', flush=True)
        finally:
            connection.close()
            with self.lock:
                self.connected = False

    def ingest(self, raw):
        """Accept only complete, finite sensor packets; never fabricate missing data."""
        now = time.monotonic()
        with self.lock:
            try:
                packet = json.loads(raw)
                if not isinstance(packet, dict):
                    raise ValueError('packet')
                if packet.get('type') == 'gateway':
                    self.gateway = {k: v for k, v in packet.items() if k != 'type'}
                    return
                if packet.get('type') == 'meta':
                    self.meta = {k: packet[k] for k in ('board', 'sensor', 'address',
                        'who_am_i', 'odr_hz', 'accel_range_g', 'gyro_range_dps') if k in packet}
                    return
                if packet.get('type') == 'error':
                    self.error = str(packet.get('message', '센서 읽기 오류'))[:300]
                    self._finish_recording('sensor_error')
                    return
                if packet.get('type') != 'sample':
                    raise ValueError('packet type')
                seq, t_us = packet['seq'], packet['t_us']
                if type(seq) is not int or type(t_us) is not int or not (0 <= seq < 2**32 and 0 <= t_us < 2**32):
                    raise ValueError('sequence or timestamp')
                if any(type(packet[k]) not in (int, float) for k in AXES):
                    raise ValueError('non-numeric axis')
                values = {k: float(packet[k]) for k in AXES}
                if any(not math.isfinite(v) for v in values.values()):
                    raise ValueError('non-finite')
                if self._last_seq is not None:
                    delta_seq = (seq - self._last_seq) % 2**32
                    delta_t = (t_us - self._last_t) % 2**32
                    if delta_seq == 0:
                        raise ValueError('duplicate')
                    if delta_seq > 2**31 or delta_t > 60_000_000:
                        self._finish_recording('device_restarted')
                        self.history.clear()
                        self._elapsed_us = 0
                    else:
                        self.dropped += delta_seq - 1
                        self._elapsed_us += delta_t
                self._last_seq, self._last_t = seq, t_us
                self._last_rx = now
                if self._first_rx is None:
                    self._first_rx = now
                self._id += 1
                sample = dict(id=self._id, seq=seq, t_us=t_us,
                              elapsed_s=self._elapsed_us / 1e6, host_time=utc_now(), **values)
                self.history.append((now, sample))
                self.count += 1
                self.error = None
                if self.recording:
                    self._writer.writerow(sample)
                    self.recording['samples'] += 1
                    if self.recording['samples'] % 208 == 0:
                        self._file.flush()
            except (ValueError, KeyError, TypeError, OverflowError, UnicodeDecodeError):
                self.invalid += 1

    def status(self):
        with self.lock:
            now = time.monotonic()
            age = now - self._last_rx if self._last_rx is not None else None
            fresh = self.connected and self.error is None and age is not None and age < 1.5
            window = [s for t, s in self.history if now - t <= 1]
            rate_window = [(t, s) for t, s in self.history if now - t <= 2]
            rate = None
            if fresh and len(rate_window) > 1:
                span = rate_window[-1][0] - rate_window[0][0]
                if span > 0:
                    rate = (len(rate_window) - 1) / span
            rms = peak = None
            if fresh and len(window) >= 2:
                means = {k: sum(s[k] for s in window)/len(window) for k in AXES[:3]}
                norms2 = [sum((s[k]-means[k])**2 for k in AXES[:3]) for s in window]
                rms, peak = math.sqrt(sum(norms2)/len(norms2)), math.sqrt(max(norms2))
            return dict(connected=self.connected, port=self.port, source=self.source,
                        gateway=dict(self.gateway),
                        board=self.meta.get('board', '미확인'), sensor=self.meta.get('sensor', '미확인'),
                        meta=dict(self.meta), error=self.error,
                        fresh=fresh, last_sample_age_s=age,
                        latest=dict(self.history[-1][1]) if self.history else None,
                        stats=dict(sample_count=self.count, sample_rate_hz=rate,
                                   dropped=self.dropped, invalid_lines=self.invalid,
                                   ac_rms_g=rms, peak_g=peak),
                        recording=dict(self.recording) if self.recording else None)

    def samples(self, after):
        with self.lock:
            return dict(samples=[s for _, s in self.history if s['id'] > after], status=self.status())

    def start_recording(self, label, condition):
        with self.lock:
            if self.recording:
                raise ValueError('이미 기록 중입니다.')
            if not self.status()['fresh']:
                raise ValueError('실제 센서 데이터가 들어온 뒤 기록을 시작해 주세요.')
            label = str(label).strip()[:80]
            condition = str(condition).strip()[:200]
            if not label:
                raise ValueError('실험 이름을 입력해 주세요.')
            rid = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
            self.recording = dict(id=rid, label=label, condition=condition, samples=0,
                                  started_at=utc_now(), ended_at=None, stop_reason=None,
                                  source='hardware', transport=self.source, port=self.port,
                                  meta=dict(self.meta), gateway=dict(self.gateway))
            self._file = (self.data_dir/f'{rid}.csv').open('w', newline='', encoding='utf-8')
            self._writer = csv.DictWriter(self._file, fieldnames=FIELDS)
            self._writer.writeheader()
            self._save_metadata()
            return dict(self.recording)

    def _save_metadata(self):
        if self.recording:
            target = self.data_dir/f"{self.recording['id']}.json"
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(self.recording, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(target)

    def _finish_recording(self, reason):
        if not self.recording:
            return None
        self.recording['ended_at'] = utc_now()
        self.recording['stop_reason'] = reason
        self._file.flush()
        self._file.close()
        self._save_metadata()
        result = dict(self.recording)
        self.recording = self._file = self._writer = None
        return result

    def stop_recording(self):
        with self.lock:
            return self._finish_recording('user_stopped')

    def recordings(self):
        result = []
        for path in sorted(self.data_dir.glob('*.json'), reverse=True):
            if ID_RE.fullmatch(path.stem):
                record = json.loads(path.read_text(encoding='utf-8'))
                if self.recording and record['id'] == self.recording['id']:
                    continue
                result.append(record)
        return result

    def get_recording(self, rid):
        if not ID_RE.fullmatch(rid):
            raise ValueError('잘못된 기록 ID입니다.')
        path = self.data_dir/f'{rid}.json'
        meta = json.loads(path.read_text(encoding='utf-8'))
        with (self.data_dir/f'{rid}.csv').open(newline='', encoding='utf-8') as stream:
            samples = []
            for row in csv.DictReader(stream):
                sample = {k: float(row[k]) for k in AXES}
                sample.update({k: int(row[k]) for k in ('id','seq','t_us')})
                sample.update(elapsed_s=float(row['elapsed_s']), host_time=row['host_time'])
                samples.append(sample)
        return dict(recording=meta, samples=samples)


def make_handler(lab):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT/'dist'), **kwargs)

        def log_message(self, fmt, *args):
            if '/api/samples' not in str(args):
                super().log_message(fmt, *args)

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _local(self):
            host = self.headers.get('Host','')
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            return host in allowed and self.headers.get('Origin', 'http://'+host) in {'http://'+h for h in allowed}

        def do_GET(self):
            if not self._local():
                return self._json({'error':'Local access only'}, 403)
            url = urlparse(self.path)
            try:
                if url.path == '/api/ports':
                    return self._json(dict(ports=ports()))
                if url.path == '/api/status':
                    return self._json(lab.status())
                if url.path == '/api/samples':
                    after = int(parse_qs(url.query).get('after',['0'])[0])
                    return self._json(lab.samples(after))
                if url.path == '/api/recordings':
                    return self._json(dict(recordings=lab.recordings()))
                if url.path.startswith('/api/recordings/'):
                    rid = url.path.rsplit('/',1)[-1]
                    if rid.endswith('.csv'):
                        rid = rid[:-4]
                        if not ID_RE.fullmatch(rid):
                            raise ValueError('잘못된 기록 ID입니다.')
                        content = (lab.data_dir/f'{rid}.csv').read_bytes()
                        self.send_response(200)
                        self.send_header('Content-Type','text/csv; charset=utf-8')
                        self.send_header('Content-Disposition',f'attachment; filename="{rid}.csv"')
                        self.send_header('Content-Length',str(len(content)))
                        self.end_headers()
                        return self.wfile.write(content)
                    return self._json(lab.get_recording(rid))
                if url.path.startswith('/api/'):
                    return self._json({'error':'Not found'},404)
                return super().do_GET()
            except FileNotFoundError:
                return self._json({'error':'기록을 찾을 수 없습니다.'},404)
            except (ValueError, KeyError) as exc:
                return self._json({'error':str(exc)},400)

        def do_POST(self):
            if not self._local() or self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                return self._json({'error':'Local JSON requests only'},403)
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 <= length <= 4096:
                    raise ValueError('요청이 너무 큽니다.')
                data = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(data,dict):
                    raise ValueError('JSON object required')
                if self.path == '/api/connect':
                    lab.connect(data.get('port'), data.get('source','serial'))
                elif self.path == '/api/disconnect':
                    lab.disconnect()
                elif self.path == '/api/record/start':
                    lab.start_recording(data.get('label',''), data.get('condition',''))
                elif self.path == '/api/record/stop':
                    lab.stop_recording()
                else:
                    return self._json({'error':'Not found'},404)
                return self._json(lab.status())
            except (ValueError, serial.SerialException, OSError) as exc:
                return self._json({'error':str(exc)},400)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--serial-port', help='Explicit sensor port. Never automatically selects a device.')
    args = parser.parse_args()
    lab = Lab(ROOT/'recordings')
    server = ThreadingHTTPServer(('127.0.0.1',args.port), make_handler(lab))
    if args.serial_port:
        lab.connect(args.serial_port)
    print(f'Local: http://127.0.0.1:{args.port}', flush=True)
    print('Actual serial data only. Press Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        lab.disconnect()
        server.server_close()


if __name__ == '__main__':
    main()
