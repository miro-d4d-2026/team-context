#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_TinyUSB.h>
#include <bluefruit.h>

// NU40DK Arduino core: Wire = SDA P0.26, SCL P0.27.
// USB JSON + BLE binary notifications. No motor or printer commands.
namespace {
constexpr uint8_t WHO_AM_I = 0x0F;
constexpr uint8_t CTRL1_XL = 0x10;
constexpr uint8_t CTRL2_G = 0x11;
constexpr uint8_t CTRL3_C = 0x12;
constexpr uint8_t STATUS_REG = 0x1E;
constexpr uint8_t OUTX_L_G = 0x22;
constexpr uint32_t RETRY_MS = 2000;
constexpr uint32_t META_MS = 2000;
constexpr float ACCEL_G_PER_LSB = 0.000122f;  // +/-4 g, 0.122 mg/LSB
constexpr float GYRO_DPS_PER_LSB = 0.01750f;  // +/-500 dps, 17.50 mdps/LSB

uint8_t imuAddress = 0;
uint8_t deviceId = 0;
bool sensorReady = false;
uint32_t sequence = 0;
uint32_t lastAttemptMs = 0;
uint32_t lastMetaMs = 0;
uint32_t lastSampleMs = 0;
bool bleReady = false;
uint32_t bleDropped = 0;
BLEService imuService("7e57a000-b792-4d7a-a252-87986150fe70");
BLECharacteristic imuData("7e57a001-b792-4d7a-a252-87986150fe70");
BLECharacteristic imuMeta("7e57a002-b792-4d7a-a252-87986150fe70");

void putUint16(uint8_t *target, uint16_t value) {
  target[0] = value & 0xFF;
  target[1] = value >> 8;
}

void putUint32(uint8_t *target, uint32_t value) {
  for (uint8_t i = 0; i < 4; ++i) target[i] = value >> (8 * i);
}

bool notifyWithoutWaiting(BLECharacteristic &characteristic,
                          const uint8_t *payload, uint16_t length) {
  const uint16_t connection = Bluefruit.connHandle();
  if (!bleReady || connection == BLE_CONN_HANDLE_INVALID ||
      !characteristic.notifyEnabled(connection)) return false;

  // Bluefruit's notify() waits for its semaphore. The SoftDevice API instead
  // returns NRF_ERROR_RESOURCES immediately when its transmission queue is full.
  ble_gatts_hvx_params_t notification = {};
  notification.handle = characteristic.handles().value_handle;
  notification.type = BLE_GATT_HVX_NOTIFICATION;
  notification.p_len = &length;
  notification.p_data = payload;
  return sd_ble_gatts_hvx(connection, &notification) == NRF_SUCCESS;
}

void reportBleMetadata() {
  if (!bleReady) return;
  // Version 1: <BBBBHHHH>, little endian. Flags reserved and always zero.
  uint8_t packet[12] = {1, (uint8_t)(sensorReady ? 1 : 0), deviceId, imuAddress};
  putUint16(packet + 4, 208);
  putUint16(packet + 6, 4);
  putUint16(packet + 8, 500);
  imuMeta.write(packet, sizeof(packet));
  notifyWithoutWaiting(imuMeta, packet, sizeof(packet));
}

bool startBluetooth() {
  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);
  if (!Bluefruit.begin(1, 0)) return false;
  Bluefruit.setName("NU40-IMU");
  Bluefruit.setTxPower(4);
  Bluefruit.autoConnLed(true);
  Bluefruit.Periph.setConnInterval(6, 12);  // 7.5-15 ms request, central decides.
  if (imuService.begin() != ERROR_NONE) return false;
  imuData.setProperties(CHR_PROPS_NOTIFY);
  imuData.setPermission(SECMODE_OPEN, SECMODE_NO_ACCESS);
  imuData.setFixedLen(20);
  if (imuData.begin() != ERROR_NONE) return false;
  imuMeta.setProperties(CHR_PROPS_READ | CHR_PROPS_NOTIFY);
  imuMeta.setPermission(SECMODE_OPEN, SECMODE_NO_ACCESS);
  imuMeta.setFixedLen(12);
  if (imuMeta.begin() != ERROR_NONE) return false;

  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addService(imuService);
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(32, 244);
  Bluefruit.Advertising.setFastTimeout(30);
  return Bluefruit.Advertising.start(0);
}

void reportBleSample(uint32_t seq, uint32_t tUs, const uint8_t *raw) {
  if (!bleReady || !Bluefruit.connected() || !imuData.notifyEnabled()) return;
  uint8_t packet[20];
  putUint32(packet, seq);
  putUint32(packet + 4, tUs);
  // Wire reads gyro then accel; BLE protocol deliberately carries accel first.
  memcpy(packet + 8, raw + 6, 6);
  memcpy(packet + 14, raw, 6);
  if (!notifyWithoutWaiting(imuData, packet, sizeof(packet))) ++bleDropped;
}

void hexByte(uint8_t value) {
  Serial.print("0x");
  if (value < 0x10) Serial.print('0');
  Serial.print(value, HEX);
}

bool readRegisters(uint8_t address, uint8_t reg, uint8_t *data, uint8_t count) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(address, count) != count) {
    while (Wire.available()) Wire.read();
    return false;
  }
  for (uint8_t i = 0; i < count; ++i) data[i] = Wire.read();
  return true;
}

bool writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(imuAddress);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

void reportError(const char *message) {
  if (!Serial) return;
  Serial.print("{\"type\":\"error\",\"message\":\"");
  Serial.print(message);
  Serial.println("\"}");
}

void reportScan() {
  if (!Serial) return;
  Serial.print("{\"type\":\"error\",\"message\":\"IMU detection or configuration failed. Check power, SDA P0.26 and SCL P0.27.\",\"addresses\":[");
  bool first = true;
  for (uint8_t address = 0x08; address <= 0x77; ++address) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() != 0) continue;
    if (!first) Serial.print(',');
    Serial.print('"');
    hexByte(address);
    Serial.print('"');
    first = false;
  }
  Serial.println("]}");
}

bool detectAndConfigure() {
  for (uint8_t candidate = 0x6A; candidate <= 0x6B; ++candidate) {
    uint8_t id = 0;
    if (!readRegisters(candidate, WHO_AM_I, &id, 1)) continue;
    if (id != 0x69 && id != 0x6A) continue;
    imuAddress = candidate;
    deviceId = id;

    // Reset only the recognized sensor, then explicitly set scales and ODR.
    if (!writeRegister(CTRL3_C, 0x01)) break;
    const uint32_t resetStartMs = millis();
    uint8_t control = 0x01;
    do {
      delay(1);
      if (!readRegisters(imuAddress, CTRL3_C, &control, 1)) break;
    } while ((control & 0x01) && (uint32_t)(millis() - resetStartMs) < 100);
    if (control & 0x01) break;

    // BDU + IF_INC; accel 208 Hz +/-4 g; gyro 208 Hz +/-500 dps.
    if (!writeRegister(CTRL3_C, 0x44) ||
        !writeRegister(CTRL1_XL, 0x58) ||
        !writeRegister(CTRL2_G, 0x54)) break;
    uint8_t configured[3];
    if (!readRegisters(imuAddress, CTRL1_XL, configured, 3) ||
        configured[0] != 0x58 || configured[1] != 0x54 || configured[2] != 0x44) {
      break;
    }
    lastSampleMs = millis();
    return true;
  }
  imuAddress = 0;
  deviceId = 0;
  return false;
}

void reportMetadata() {
  reportBleMetadata();
  if (!Serial) return;
  Serial.print("{\"type\":\"meta\",\"board\":\"NU-40\",\"sensor\":\"");
  Serial.print(deviceId == 0x6A ? "LSM6DS3TR-C" :
               (deviceId == 0x69 ? "LSM6DS3TR" : "unavailable"));
  Serial.print("\",\"address\":\"");
  hexByte(imuAddress);
  Serial.print("\",\"who_am_i\":\"");
  hexByte(deviceId);
  Serial.print("\",\"odr_hz\":208,\"accel_range_g\":4,\"gyro_range_dps\":500,\"timestamp\":\"read_start_micros_uint32\",\"sensor_ready\":");
  Serial.print(sensorReady ? "true" : "false");
  Serial.print(",\"ble_ready\":");
  Serial.print(bleReady ? "true" : "false");
  Serial.print(",\"ble_dropped\":");
  Serial.print(bleDropped);
  Serial.println('}');
}

int16_t littleEndianInt16(const uint8_t *data) {
  return (int16_t)((uint16_t)data[0] | ((uint16_t)data[1] << 8));
}

void reportSample(uint32_t seq, uint32_t tUs, const uint8_t *raw) {
  if (!Serial) return;
  Serial.print("{\"type\":\"sample\",\"seq\":");
  Serial.print(seq);
  Serial.print(",\"t_us\":");
  Serial.print(tUs);
  Serial.print(",\"ax_g\":");
  Serial.print(littleEndianInt16(raw + 6) * ACCEL_G_PER_LSB, 6);
  Serial.print(",\"ay_g\":");
  Serial.print(littleEndianInt16(raw + 8) * ACCEL_G_PER_LSB, 6);
  Serial.print(",\"az_g\":");
  Serial.print(littleEndianInt16(raw + 10) * ACCEL_G_PER_LSB, 6);
  Serial.print(",\"gx_dps\":");
  Serial.print(littleEndianInt16(raw) * GYRO_DPS_PER_LSB, 4);
  Serial.print(",\"gy_dps\":");
  Serial.print(littleEndianInt16(raw + 2) * GYRO_DPS_PER_LSB, 4);
  Serial.print(",\"gz_dps\":");
  Serial.print(littleEndianInt16(raw + 4) * GYRO_DPS_PER_LSB, 4);
  Serial.println('}');
}
}  // namespace

void setup() {
  Serial.begin(115200);
  bleReady = startBluetooth();
  if (!bleReady) reportError("BLE initialization failed; USB acquisition remains available.");
  Wire.begin();
  Wire.setClock(400000);
  // Never wait forever for the host. Samples are drained without USB consumers.
  delay(100);
  sensorReady = detectAndConfigure();
  lastAttemptMs = millis();
  reportMetadata();
  if (!sensorReady) reportScan();
}

void loop() {
  const uint32_t nowMs = millis();
  if ((uint32_t)(nowMs - lastMetaMs) >= META_MS) {
    reportMetadata();
    lastMetaMs = nowMs;
  }
  if (!sensorReady) {
    if ((uint32_t)(nowMs - lastAttemptMs) >= RETRY_MS) {
      lastAttemptMs = nowMs;
      sensorReady = detectAndConfigure();
      reportMetadata();
      if (!sensorReady) reportScan();
    }
    delay(1);
    return;
  }

  uint8_t status = 0;
  if (!readRegisters(imuAddress, STATUS_REG, &status, 1)) {
    reportError("IMU status read failed; retrying detection.");
    sensorReady = false;
    reportBleMetadata();
    lastAttemptMs = nowMs;
    return;
  }
  // Require both accelerometer and gyroscope new-data flags.
  if ((status & 0x03) != 0x03) {
    if ((uint32_t)(nowMs - lastSampleMs) >= 1000) {
      reportError("IMU produced no new sample for one second; retrying detection.");
      sensorReady = false;
      reportBleMetadata();
      lastAttemptMs = nowMs;
    }
    delayMicroseconds(100);
    return;
  }

  uint8_t raw[12];
  // Host-read timestamp, NOT the sensor's hardware timestamp. Wraps at 2^32 us.
  const uint32_t tUs = micros();
  if (!readRegisters(imuAddress, OUTX_L_G, raw, sizeof(raw))) {
    reportError("IMU sample read failed; sample omitted, retrying detection.");
    sensorReady = false;
    reportBleMetadata();
    lastAttemptMs = nowMs;
    return;
  }
  lastSampleMs = millis();
  const uint32_t seq = sequence++;
  reportBleSample(seq, tUs, raw);
  reportSample(seq, tUs, raw);
}
