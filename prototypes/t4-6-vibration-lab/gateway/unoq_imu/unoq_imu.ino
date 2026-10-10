#include <Arduino.h>
#include <Wire.h>
#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>
#include <math.h>

// UNO Q Qwiic: Wire1 / I2C4, PD12 SCL and PD13 SDA, 3.3 V.
// Acquisition stays on the MCU. Linux reads bounded binary batches over Bridge.
namespace {
constexpr uint8_t WHO_AM_I = 0x0F;
constexpr uint8_t CTRL1_XL = 0x10;
constexpr uint8_t CTRL2_G = 0x11;
constexpr uint8_t CTRL3_C = 0x12;
constexpr uint8_t STATUS_REG = 0x1E;
constexpr uint8_t OUTX_L_G = 0x22;
constexpr uint16_t CAPACITY = 256;
constexpr uint8_t SAMPLE_BYTES = 20;
constexpr uint8_t BATCH_SAMPLES = 8;
constexpr float ACCEL_G_PER_LSB = 0.000122f;
constexpr float DISPLAY_FULL_SCALE_G = 0.25f;

Arduino_LED_Matrix matrix;
uint8_t frame[8 * 13] = {};
struct k_mutex stateMutex;
uint8_t queue[CAPACITY][SAMPLE_BYTES];
uint16_t queueHead = 0;
uint16_t queueCount = 0;
uint32_t ringOverflow = 0;
uint32_t readErrors = 0;
uint32_t sequence = 0;
uint8_t imuAddress = 0;
uint8_t deviceId = 0;
bool sensorReady = false;
bool ledInitialized = false;
uint8_t ledLevel = 0;
uint32_t rmsMicroG = 0;
uint32_t lastAttemptMs = 0;
uint32_t lastSampleMs = 0;
uint32_t lastDisplayMs = 0;
uint32_t windowStartMs = 0;
uint16_t windowCount = 0;
float mean[3] = {};
float varianceSum[3] = {};

void putUint16(uint8_t *target, uint16_t value) {
  target[0] = value & 0xFF;
  target[1] = value >> 8;
}

void putUint32(uint8_t *target, uint32_t value) {
  for (uint8_t i = 0; i < 4; ++i) target[i] = value >> (8 * i);
}

int16_t littleEndianInt16(const uint8_t *data) {
  return (int16_t)((uint16_t)data[0] | ((uint16_t)data[1] << 8));
}

bool readRegisters(uint8_t address, uint8_t reg, uint8_t *data, uint8_t count) {
  Wire1.beginTransmission(address);
  Wire1.write(reg);
  if (Wire1.endTransmission(false) != 0) return false;
  if (Wire1.requestFrom(address, count) != count) {
    while (Wire1.available()) Wire1.read();
    return false;
  }
  for (uint8_t i = 0; i < count; ++i) data[i] = Wire1.read();
  return true;
}

bool writeRegister(uint8_t address, uint8_t reg, uint8_t value) {
  Wire1.beginTransmission(address);
  Wire1.write(reg);
  Wire1.write(value);
  return Wire1.endTransmission() == 0;
}

void resetRmsWindow() {
  windowStartMs = millis();
  windowCount = 0;
  memset(mean, 0, sizeof(mean));
  memset(varianceSum, 0, sizeof(varianceSum));
}

void markUnavailable() {
  k_mutex_lock(&stateMutex, K_FOREVER);
  sensorReady = false;
  imuAddress = 0;
  deviceId = 0;
  queueCount = 0;
  ledLevel = 0;
  rmsMicroG = 0;
  ++readErrors;
  k_mutex_unlock(&stateMutex);
  resetRmsWindow();
  lastAttemptMs = millis();
}

bool detectAndConfigure() {
  for (uint8_t address = 0x6A; address <= 0x6B; ++address) {
    uint8_t id = 0;
    if (!readRegisters(address, WHO_AM_I, &id, 1) ||
        (id != 0x69 && id != 0x6A)) continue;
    if (!writeRegister(address, CTRL3_C, 0x01)) continue;
    const uint32_t started = millis();
    uint8_t control = 1;
    do {
      delay(1);
      if (!readRegisters(address, CTRL3_C, &control, 1)) break;
    } while ((control & 1) && (uint32_t)(millis() - started) < 100);
    if (control & 1) continue;
    // BDU + increment; 208 Hz accel +/-4 g; 208 Hz gyro +/-500 dps.
    if (!writeRegister(address, CTRL3_C, 0x44) ||
        !writeRegister(address, CTRL1_XL, 0x58) ||
        !writeRegister(address, CTRL2_G, 0x54)) continue;
    uint8_t configured[3];
    if (!readRegisters(address, CTRL1_XL, configured, 3) ||
        configured[0] != 0x58 || configured[1] != 0x54 || configured[2] != 0x44) continue;
    k_mutex_lock(&stateMutex, K_FOREVER);
    imuAddress = address;
    deviceId = id;
    sensorReady = true;
    k_mutex_unlock(&stateMutex);
    lastSampleMs = millis();
    resetRmsWindow();
    return true;
  }
  markUnavailable();
  return false;
}

// MsgPack bin_t<uint8_t> is encoded as binary and decoded as Python bytes.
MsgPack::bin_t<uint8_t> imu_meta() {
  MsgPack::bin_t<uint8_t> packet(12, 0);
  k_mutex_lock(&stateMutex, K_FOREVER);
  packet[0] = 1;
  packet[1] = sensorReady ? 1 : 0;
  packet[2] = deviceId;
  packet[3] = imuAddress;
  k_mutex_unlock(&stateMutex);
  putUint16(packet.data() + 4, 208);
  putUint16(packet.data() + 6, 4);
  putUint16(packet.data() + 8, 500);
  return packet;
}

MsgPack::bin_t<uint8_t> imu_batch() {
  MsgPack::bin_t<uint8_t> batch;
  batch.reserve(BATCH_SAMPLES * SAMPLE_BYTES);
  k_mutex_lock(&stateMutex, K_FOREVER);
  const uint8_t count = queueCount < BATCH_SAMPLES ? queueCount : BATCH_SAMPLES;
  for (uint8_t i = 0; i < count; ++i) {
    batch.insert(batch.end(), queue[queueHead], queue[queueHead] + SAMPLE_BYTES);
    queueHead = (queueHead + 1) % CAPACITY;
  }
  queueCount -= count;
  k_mutex_unlock(&stateMutex);
  return batch;
}

// Python list: ready, LED init, acquired, ring overflows, queued, read errors,
// display level, and last completed one-second RMS in micro-g.
MsgPack::arr_t<uint32_t> imu_status() {
  MsgPack::arr_t<uint32_t> result(8, 0);
  k_mutex_lock(&stateMutex, K_FOREVER);
  result[0] = sensorReady;
  result[1] = ledInitialized;
  result[2] = sequence;
  result[3] = ringOverflow;
  result[4] = queueCount;
  result[5] = readErrors;
  result[6] = ledLevel;
  result[7] = rmsMicroG;
  k_mutex_unlock(&stateMutex);
  return result;
}

void enqueueSample(uint32_t tUs, const uint8_t *raw) {
  k_mutex_lock(&stateMutex, K_FOREVER);
  if (queueCount == CAPACITY) {
    queueHead = (queueHead + 1) % CAPACITY;
    --queueCount;
    ++ringOverflow;
  }
  uint8_t *packet = queue[(queueHead + queueCount) % CAPACITY];
  putUint32(packet, sequence++);
  putUint32(packet + 4, tUs);
  memcpy(packet + 8, raw + 6, 6);  // Accelerometer first in wire protocol.
  memcpy(packet + 14, raw, 6);
  ++queueCount;
  k_mutex_unlock(&stateMutex);
}

void updateRms(const uint8_t *raw) {
  ++windowCount;
  for (uint8_t axis = 0; axis < 3; ++axis) {
    const float value = littleEndianInt16(raw + 6 + 2 * axis) * ACCEL_G_PER_LSB;
    const float difference = value - mean[axis];
    mean[axis] += difference / windowCount;
    varianceSum[axis] += difference * (value - mean[axis]);
  }
  if ((uint32_t)(millis() - windowStartMs) < 1000 || windowCount < 2) return;
  const float variance = (varianceSum[0] + varianceSum[1] + varianceSum[2]) / windowCount;
  const float rms = sqrtf(variance > 0 ? variance : 0);
  k_mutex_lock(&stateMutex, K_FOREVER);
  // RMS is nonnegative. Avoid lroundf, unavailable in the board's 0.51 loader.
  rmsMicroG = (uint32_t)(rms * 1000000.0f + 0.5f);
  ledLevel = constrain((int)(rms / DISPLAY_FULL_SCALE_G * 8 + 0.5f), 0, 8);
  k_mutex_unlock(&stateMutex);
  resetRmsWindow();
}

void updateDisplay(uint32_t now) {
  if ((uint32_t)(now - lastDisplayMs) < 100) return;
  lastDisplayMs = now;
  memset(frame, 0, sizeof(frame));
  if (sensorReady && (uint32_t)(now - lastSampleMs) < 300) {
    frame[3 * 13 + 1] = (now / 500) % 2 ? 7 : 2;
    frame[4 * 13 + 1] = frame[3 * 13 + 1];
    for (int y = 7; y >= 8 - ledLevel; --y) {
      for (uint8_t x = 4; x < 13; ++x) frame[y * 13 + x] = 5;
    }
  } else {
    const uint8_t brightness = (now / 700) % 2 ? 4 : 1;
    for (uint8_t x = 4; x <= 8; ++x) {
      frame[1 * 13 + x] = brightness;
      frame[6 * 13 + x] = brightness;
    }
    for (uint8_t y = 1; y <= 6; ++y) {
      frame[y * 13 + 4] = brightness;
      frame[y * 13 + 8] = brightness;
    }
  }
  matrix.draw(frame);
}
}  // namespace

void setup() {
  k_mutex_init(&stateMutex);
  ledInitialized = matrix.begin() == 1;
  matrix.setGrayscaleBits(3);
  matrix.clear();
  Wire1.begin();
  Wire1.setClock(400000);
  delay(100);
  detectAndConfigure();
  lastAttemptMs = millis();
  Bridge.begin();
  Bridge.provide("imu_meta", imu_meta);
  Bridge.provide("imu_batch", imu_batch);
  Bridge.provide("imu_status", imu_status);
}

void loop() {
  const uint32_t now = millis();
  updateDisplay(now);
  if (!sensorReady) {
    if ((uint32_t)(now - lastAttemptMs) >= 2000) {
      lastAttemptMs = now;
      detectAndConfigure();
    }
    delay(1);
    return;
  }
  uint8_t status;
  if (!readRegisters(imuAddress, STATUS_REG, &status, 1)) {
    markUnavailable();
    return;
  }
  if ((status & 3) != 3) {
    if ((uint32_t)(now - lastSampleMs) >= 1000) markUnavailable();
    delayMicroseconds(100);
    return;
  }
  uint8_t raw[12];
  const uint32_t tUs = micros();
  if (!readRegisters(imuAddress, OUTX_L_G, raw, sizeof(raw))) {
    markUnavailable();
    return;
  }
  lastSampleMs = millis();
  enqueueSample(tUs, raw);
  updateRms(raw);
}
