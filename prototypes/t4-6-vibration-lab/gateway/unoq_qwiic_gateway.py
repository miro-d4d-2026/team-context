#!/usr/bin/env python3
"""UNO Q MCU Qwiic IMU -> Arduino Router -> USB-forwarded loopback JSONL."""
import argparse
import asyncio
import contextlib
import json
import struct
import time

SAMPLE = struct.Struct('<IIhhhhhh')
META = struct.Struct('<BBBBHHHH')
MAX_BATCH_SAMPLES = 8


def decode_meta(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) != META.size:
        raise ValueError('imu_meta must return 12 binary bytes')
    version, ready, who, address, odr, accel, gyro, flags = META.unpack(data)
    if version != 1 or ready not in (0, 1) or flags != 0:
        raise ValueError('Unsupported MCU IMU protocol')
    if (odr, accel, gyro) != (208, 4, 500):
        raise ValueError('Unexpected IMU range; refusing incorrect unit conversion')
    if ready and (who not in (0x69, 0x6A) or address not in (0x6A, 0x6B)):
        raise ValueError('Unexpected IMU identity')
    sensor = ('LSM6DS3TR-C' if who == 0x6A else 'LSM6DS3TR') if ready else '미확인'
    return dict(type='meta', board='Arduino UNO Q', sensor=sensor,
                address=f'0x{address:02X}', who_am_i=f'0x{who:02X}', odr_hz=odr,
                accel_range_g=accel, gyro_range_dps=gyro), bool(ready)


def decode_batch(data):
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError('imu_batch must return binary bytes')
    if len(data) % SAMPLE.size or len(data) > SAMPLE.size * MAX_BATCH_SAMPLES:
        raise ValueError('imu_batch has incomplete or oversized sample records')
    samples = []
    for seq, t_us, ax, ay, az, gx, gy, gz in SAMPLE.iter_unpack(data):
        samples.append(dict(type='sample', seq=seq, t_us=t_us,
                            ax_g=ax * 0.000122, ay_g=ay * 0.000122, az_g=az * 0.000122,
                            gx_dps=gx * 0.01750, gy_dps=gy * 0.01750, gz_dps=gz * 0.01750))
    return samples


def decode_status(data):
    if (not isinstance(data, list) or len(data) != 8
            or any(type(value) is not int or not 0 <= value < 2**32 for value in data)):
        raise ValueError('imu_status must return eight uint32 values')
    ready, led, acquired, overflows, depth, read_errors, level, rms_micro_g = data
    if ready not in (0, 1) or led not in (0, 1) or level > 8 or depth > 256:
        raise ValueError('Invalid MCU status')
    return dict(sensor_ready=bool(ready), led_ready=bool(led),
                led_verification='MCU matrix.begin() result; physical visibility unverified',
                acquired_count=acquired, ring_overflow_count=overflows,
                queue_depth=depth, read_error_count=read_errors, led_level=level,
                mcu_rms_g=rms_micro_g / 1_000_000)


class Gateway:
    def __init__(self):
        self.clients = set()
        self.meta = None
        self.mcu_status = {}
        self.router_connected = False
        self.sensor_ready = False
        self.sample_count = 0
        self.last_sample_at = None
        self.error = 'UNO Q MCU 센서 연결을 기다리고 있습니다.'

    def status(self):
        age = None if self.last_sample_at is None else time.monotonic() - self.last_sample_at
        status = dict(type='gateway', board='Arduino UNO Q', transport='QWIIC',
                      router_connected=self.router_connected, sensor_ready=self.sensor_ready,
                      sample_count=self.sample_count, last_sample_age_s=age,
                      led_ready=False, led_error='MCU 상태 확인 전')
        status.update(self.mcu_status)
        if not self.router_connected:
            status.update(sensor_ready=False, led_ready=False,
                          led_error='MCU 통신을 확인할 수 없습니다.')
        elif self.mcu_status:
            status['led_error'] = None if status['led_ready'] else 'MCU 매트릭스 초기화 실패'
        return status

    def emit(self, packet):
        line = (json.dumps(packet, ensure_ascii=False, allow_nan=False) + '\n').encode()
        for queue, writer in tuple(self.clients):
            try:
                queue.put_nowait(line)
            except asyncio.QueueFull:
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
        if not self.sensor_ready:
            self.set_error('UNO Q의 Qwiic IMU를 읽을 수 없습니다. 케이블과 센서를 확인하세요.')

    def receive_batch(self, data):
        samples = decode_batch(data)
        if samples and not self.sensor_ready:
            raise ValueError('Samples arrived before valid ready metadata')
        if samples:
            self.last_sample_at = time.monotonic()
            self.sample_count += len(samples)
            self.error = None
            for sample in samples:
                self.emit(sample)
        return len(samples)

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
                    with contextlib.suppress(asyncio.CancelledError, ConnectionError, OSError):
                        await task
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def poll(self, bridge):
        next_info = 0.0
        while True:
            started = time.monotonic()
            try:
                if started >= next_info:
                    self.receive_meta(await asyncio.to_thread(bridge.call, 'imu_meta', timeout=1))
                    self.mcu_status = decode_status(
                        await asyncio.to_thread(bridge.call, 'imu_status', timeout=1))
                    self.sensor_ready = self.sensor_ready and self.mcu_status['sensor_ready']
                    self.router_connected = True
                    self.emit(self.status())
                    next_info = time.monotonic() + 1
                count = self.receive_batch(
                    await asyncio.to_thread(bridge.call, 'imu_batch', timeout=1))
                if (self.last_sample_at is not None
                        and time.monotonic() - self.last_sample_at > 2 and self.error is None):
                    self.set_error('MCU는 응답하지만 IMU 샘플이 멈췄습니다.')
                # Eight-sample full batches drain immediately. Smaller batches target 40 polls/s.
                delay = 0 if count == MAX_BATCH_SAMPLES else (
                    0.01 if count == 0 else max(0, 0.025 - (time.monotonic() - started)))
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except (ConnectionError, TimeoutError, ValueError, OSError, RuntimeError) as exc:
                self.router_connected = False
                self.sensor_ready = False
                self.set_error(f'UNO Q MCU 연결 대기: {exc}')
                self.emit(self.status())
                next_info = 0
                await asyncio.sleep(1)


async def run(args):
    from arduino.router_bridge import Bridge

    gateway = Gateway()
    bridge = Bridge()
    server = await asyncio.start_server(gateway.client_connected, '127.0.0.1', args.port)
    print(f'UNO Q Qwiic gateway: 127.0.0.1:{args.port}; actual MCU samples only', flush=True)
    try:
        await asyncio.to_thread(bridge.connect, timeout=3)
        async with server:
            await gateway.poll(bridge)
    finally:
        await asyncio.to_thread(bridge.disconnect)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=9877)
    try:
        asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        pass
