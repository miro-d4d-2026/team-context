# NU40-IMU BLE protocol v1

The sketch streams the same acquired samples over USB JSON and BLE. BLE requires no USB host, pairing, or write commands. This is an unencrypted local prototype telemetry service, not a deployment security design. There is one BLE central connection at a time; advertising restarts after disconnect.

Device name: `NU40-IMU` (scan response).

| Item | UUID | Properties |
| --- | --- | --- |
| Service | `7e57a000-b792-4d7a-a252-87986150fe70` | Advertised |
| Samples | `7e57a001-b792-4d7a-a252-87986150fe70` | Notify, 20 bytes |
| Metadata | `7e57a002-b792-4d7a-a252-87986150fe70` | Read + notify, 12 bytes |

## Samples

Little-endian Python structure: `<IIhhhhhh`.

| Byte offset | Type | Value |
| --- | --- | --- |
| 0 | uint32 | Acquired sample sequence |
| 4 | uint32 | Board `micros()` at I²C sample-read start |
| 8, 10, 12 | int16 × 3 | Raw accelerometer X, Y, Z |
| 14, 16, 18 | int16 × 3 | Raw gyroscope X, Y, Z |

Version 1 uses acceleration `raw × 0.000122 g` and gyroscope `raw × 0.01750 degrees/s`. The sensor is configured to 208 Hz, ±4 g, ±500 degrees/s. The order differs from the sensor register burst, which has gyro first; the firmware reorders the bytes before sending.

Read and validate metadata before accepting samples. A gateway must not accept unknown versions or unexpected scale/ODR values using these conversion constants. No sample is emitted when the sensor read fails. USB and BLE carry the same sequence and board timestamp for a given sample.

## Metadata

Little-endian Python structure: `<BBBBHHHH`.

| Byte offset | Type | Value |
| --- | --- | --- |
| 0 | uint8 | Version: 1 |
| 1 | uint8 | Status: 0 = unavailable, 1 = configured and reading |
| 2 | uint8 | WHO_AM_I, normally 0x69 or 0x6A; 0 if undetected |
| 3 | uint8 | I²C address, normally 0x6A or 0x6B; 0 if undetected |
| 4 | uint16 | Configured ODR: 208 Hz |
| 6 | uint16 | Accelerometer range: 4 g |
| 8 | uint16 | Gyroscope range: 500 degrees/s |
| 10 | uint16 | Reserved flags: 0 |

The value is updated at startup, sensor failures, detection retries, and every two seconds. A subscriber gets notifications; the value can also be read directly. If status is zero, treat the sensor as unavailable regardless of range values. If the I²C bus physically stalls, firmware may stop, so a gateway must additionally detect stale sample timestamps rather than trust a previous healthy metadata value.

## Timing and delivery

The 20-byte sample fits the default ATT notification payload without MTU negotiation. 208 samples/s requires 4,160 payload bytes/s; this is a target, not a measured wireless guarantee. The peripheral requests a 7.5–15 ms connection interval; the central selects the actual interval.

The sketch calls the SoftDevice `sd_ble_gatts_hvx` API directly after configuring characteristics with Bluefruit. Its queue-full response returns without waiting. Bluefruit's higher-level `notify()` can wait for a packet semaphore and therefore is not used in the acquisition path. Failed notification attempts are dropped, with no unbounded queue or timestamp reconstruction. The USB metadata field `ble_dropped` counts these failures while a BLE subscriber is active. A BLE gateway counts missing sequence numbers in received notifications. Packets absent before subscription are not meaningful transport-loss evidence.

Both counters wrap at 2^32; the timestamp wraps after approximately 71.6 minutes and resets after a reboot. Use modulo subtraction and separately detect a new connection/reboot. Without sensor FIFO, the sample sequence cannot reveal conversions missed inside the IMU: also inspect actual timestamp intervals. These signals are sensor measurements, not diagnoses or remaining-life estimates.

## Build

From the project directory:

```sh
arduino-cli compile --fqbn nucode:nrf52:nu40dk --output-dir firmware/nu40_imu/build firmware/nu40_imu
```

The NU40 core includes Bluefruit and its S140 6.1.1 SoftDevice API. No additional BLE library is required. Build success does not confirm wireless connectivity or range. The base firmware README describes the USB record fields and hardware pins; this document adds the wireless path.

Sources: [Adafruit Bluefruit characteristic API](https://learn.adafruit.com/bluefruit-nrf52-feather-learning-guide/blecharacteristic), plus the installed NU40 1.0.2 `Bluefruit52Lib/src/BLECharacteristic.cpp`, `BLEConnection.cpp`, and Nordic `s140_nrf52_6.1.1_API/include/ble_gatts.h`.
