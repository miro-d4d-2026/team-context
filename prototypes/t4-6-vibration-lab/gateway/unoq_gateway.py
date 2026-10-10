#!/usr/bin/env python3
"""NU-40 BLE -> UNO Q Linux -> loopback JSONL, with MCU matrix feedback."""
import argparse
import asyncio
import contextlib
import json
import math
import struct
import time
from collections import deque

SERVICE_UUID = '7e57a000-b792-4d7a-a252-87986150fe70'
DATA_UUID = '7e57a001-b792-4d7a-a252-87986150fe70'
META_UUID = '7e57a002-b792-4d7a-a252-87986150fe70'
DATA_FORMAT = struct.Struct('<IIhhhhhh')
META_FORMAT = struct.Struct('<BBBBHHHH')


def decode_meta(data):
    if len(data) != META_FORMAT.size:
        raise ValueError('BLE metadata must contain exactly 12 bytes')
    version, status, who, address, odr, accel_range, gyro_range, flags = META_FORMAT.unpack(data)
    if version != 1 or status not in (0, 1) or flags != 0:
        raise ValueError('Unsupported NU-40 BLE protocol')
    if (odr, accel_range, gyro_range) != (208, 4, 500):
        raise ValueError('Unexpected IMU configuration; refusing incorrect unit conversion')
    if status and (who not in (0x69, 0x6A) or address not in (0x6A, 0x6B)):
        raise ValueError('Unexpected IMU identity')
    return dict(type='meta', board='NU-40',
                sensor='LSM6DS3TR-C' if who == 0x6A else 'LSM6DS3TR',
                address=f'0x{address:02X}', who_am_i=f'0x{who:02X}',
                odr_hz=odr, accel_range_g=accel_range, gyro_range_dps=gyro_range), bool(status)


def decode_sample(data):
    if len(data) != DATA_FORMAT.size:
        raise ValueError('BLE sample must contain exactly 20 bytes')
    seq, t_us, ax, ay, az, gx, gy, gz = DATA_FORMAT.unpack(data)
    return dict(type='sample', seq=seq, t_us=t_us,
                ax_g=ax * 0.000122, ay_g=ay * 0.000122, az_g=az * 0.000122,
                gx_dps=gx * 0.01750, gy_dps=gy * 0.01750, gz_dps=gz * 0.01750)


def ac_rms(samples):
    if len(samples) < 2:
        return 0.0
    axes = ('ax_g', 'ay_g', 'az_g')
    means = {axis: sum(s[axis] for s in samples) / len(samples) for axis in axes}
    return math.sqrt(sum(sum((s[a] - means[a]) ** 2 for a in axes)
                         for s in samples) / len(samples))


class Gateway:
    def __init__(self, address=None, no_led=False):
        self.address = address
        self.no_led = no_led
        self.clients = set()
        self.meta = None
        self.sensor_ready = False
        self.ble_connected = False
        self.ble_address = None
        self.led_ready = False
        self.led_error = 'LED disabled by --no-led' if no_led else None
        self.last_sample_at = None
        self.connected_at = None
        self.sample_count = 0
        self.invalid_packets = 0
        self.window = deque(maxlen=500)
        self.error = 'UNO Q가 NU40-IMU 센서를 찾는 중입니다.'

    def status(self):
        age = None if self.last_sample_at is None else time.monotonic() - self.last_sample_at
        return dict(type='gateway', board='Arduino UNO Q', transport='BLE',
                    ble_connected=self.ble_connected, ble_address=self.ble_address,
                    sensor_ready=self.sensor_ready, sample_count=self.sample_count,
                    last_sample_age_s=age, invalid_packets=self.invalid_packets,
                    led_ready=self.led_ready, led_error=self.led_error,
                    led_full_scale_g=0.25)

    def emit(self, packet):
        line = (json.dumps(packet, ensure_ascii=False, allow_nan=False) + '\n').encode()
        for queue, writer in tuple(self.clients):
            try:
                queue.put_nowait(line)
            except asyncio.QueueFull:
                # A slow reader gets a disconnected stream, never silently skipped samples.
                self.clients.discard((queue, writer))
                writer.close()

    def set_error(self, message):
        if message != self.error:
            print(message, flush=True)
        self.error = message
        self.emit(dict(type='error', message=message))

    def receive_meta(self, data):
        self.meta, self.sensor_ready = decode_meta(data)
        self.emit(self.meta)
        self.emit(self.status())
        if not self.sensor_ready:
            self.set_error('NU-40에 연결된 IMU를 읽을 수 없습니다. I2C 연결을 확인하세요.')

    def receive_sample(self, data):
        if not self.sensor_ready:
            raise ValueError('IMU metadata is not ready')
        sample = decode_sample(data)
        now = time.monotonic()
        self.last_sample_at = now
        self.sample_count += 1
        self.error = None
        self.window.append((now, sample))
        self.emit(sample)

    def led_state(self):
        now = time.monotonic()
        fresh = (self.ble_connected and self.sensor_ready and self.last_sample_at is not None
                 and now - self.last_sample_at < 1.5 and self.error is None)
        if not fresh:
            return 0, 0
        recent = [sample for timestamp, sample in self.window if now - timestamp <= 1]
        level = max(0, min(8, int(ac_rms(recent) / 0.25 * 8)))
        return 1, level

    async def client_connected(self, reader, writer):
        queue = asyncio.Queue(maxsize=1000)
        client = (queue, writer)
        self.clients.add(client)
        for packet in (self.status(), self.meta,
                       dict(type='error', message=self.error) if self.error else None):
            if packet:
                queue.put_nowait((json.dumps(packet, ensure_ascii=False) + '\n').encode())
        peer_closed = asyncio.create_task(reader.read())
        next_line = None
        try:
            while not writer.is_closing():
                next_line = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((next_line, peer_closed),
                                             return_when=asyncio.FIRST_COMPLETED)
                if peer_closed in done:
                    break
                writer.write(next_line.result())
                await asyncio.wait_for(writer.drain(), timeout=3)
        except (ConnectionError, asyncio.TimeoutError, OSError):
            pass
        finally:
            self.clients.discard(client)
            for task in (peer_closed, next_line):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def heartbeat(self):
        while True:
            if self.ble_connected:
                last = self.last_sample_at if self.last_sample_at is not None else self.connected_at
                if last is not None and time.monotonic() - last > 2 and self.error is None:
                    self.set_error('BLE는 연결되어 있지만 IMU 데이터가 멈췄습니다.')
            self.emit(self.status())
            if self.meta is not None and self.ble_connected:
                self.emit(self.meta)
            await asyncio.sleep(1)

    async def ble_loop(self):
        from bleak import BleakClient, BleakScanner

        while True:
            disconnected = asyncio.Event()
            try:
                self.set_error('UNO Q가 NU40-IMU 센서를 찾는 중입니다.')
                found = await BleakScanner.discover(timeout=5, return_adv=True,
                                                   service_uuids=[SERVICE_UUID])
                matches = []
                for device, advertisement in found.values():
                    if self.address:
                        match = device.address.lower() == self.address.lower()
                    else:
                        match = advertisement.local_name == 'NU40-IMU'
                    if match:
                        matches.append(device)
                if not matches:
                    await asyncio.sleep(1)
                    continue
                if len(matches) > 1:
                    addresses = ', '.join(d.address for d in matches)
                    raise RuntimeError(f'여러 NU-40 발견. --address로 선택하세요: {addresses}')
                device = matches[0]
                async with BleakClient(device, disconnected_callback=lambda _: disconnected.set(),
                                       timeout=15) as client:
                    self.ble_connected = True
                    self.ble_address = device.address
                    self.connected_at = time.monotonic()
                    self.last_sample_at = None
                    self.window.clear()
                    self.receive_meta(await client.read_gatt_char(META_UUID))

                    def meta_notify(_characteristic, data):
                        try:
                            self.receive_meta(data)
                        except ValueError as exc:
                            self.sensor_ready = False
                            self.invalid_packets += 1
                            self.set_error(str(exc))

                    def data_notify(_characteristic, data):
                        try:
                            self.receive_sample(data)
                        except ValueError as exc:
                            self.invalid_packets += 1
                            self.set_error(str(exc))

                    await client.start_notify(META_UUID, meta_notify)
                    await client.start_notify(DATA_UUID, data_notify)
                    print(f'BLE connected: {device.address}', flush=True)
                    await disconnected.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.set_error(f'BLE 연결 대기: {exc}')
            finally:
                self.ble_connected = False
                self.sensor_ready = False
                self.emit(self.status())
            self.set_error('NU-40 BLE 연결이 끊겼습니다. 다시 연결합니다.')
            await asyncio.sleep(2)

    async def led_loop(self):
        if self.no_led:
            return
        try:
            from arduino.router_bridge import Bridge
        except ImportError as exc:
            self.led_error = str(exc)
            self.emit(self.status())
            return
        bridge = Bridge()
        try:
            await asyncio.to_thread(bridge.connect, timeout=2)
            while True:
                try:
                    state, level = self.led_state()
                    result = await asyncio.to_thread(bridge.call, 'imu_display', state, level, timeout=1)
                    if result != state:
                        raise ValueError('Unexpected MCU LED acknowledgement')
                    self.led_ready = True
                    self.led_error = None
                    await asyncio.sleep(0.2)
                except (ConnectionError, TimeoutError, ValueError, OSError) as exc:
                    self.led_ready = False
                    self.led_error = str(exc)
                    self.emit(self.status())
                    await asyncio.sleep(1)
        finally:
            self.led_ready = False
            await asyncio.to_thread(bridge.disconnect)


async def run(args):
    gateway = Gateway(args.address, args.no_led)
    server = await asyncio.start_server(gateway.client_connected, '127.0.0.1', args.port)
    print(f'UNO Q gateway: 127.0.0.1:{args.port}; real BLE samples only', flush=True)
    tasks = [asyncio.create_task(gateway.ble_loop()),
             asyncio.create_task(gateway.led_loop()),
             asyncio.create_task(gateway.heartbeat())]
    try:
        async with server:
            await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=9877)
    parser.add_argument('--address', help='NU-40 BLE address when more than one sensor is nearby')
    parser.add_argument('--no-led', action='store_true', help='Explicit BLE-only diagnostic mode')
    try:
        asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        pass
