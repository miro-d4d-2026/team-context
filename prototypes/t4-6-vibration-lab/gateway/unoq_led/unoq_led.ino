#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>

Arduino_LED_Matrix matrix;
uint8_t frame[8 * 13] = {0};
volatile int sensorState = 0;
volatile int vibrationLevel = 0;
volatile unsigned long lastUpdate = 0;

// Called by UNO Q Linux via the router. State 1 means recent real IMU samples.
int imu_display(int state, int level) {
  sensorState = state == 1 ? 1 : 0;
  vibrationLevel = constrain(level, 0, 8);
  lastUpdate = millis();
  return sensorState;
}

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.clear();
  Bridge.begin();
  Bridge.provide("imu_display", imu_display);
}

void loop() {
  memset(frame, 0, sizeof(frame));
  const unsigned long now = millis();
  const bool fresh = sensorState == 1 && (unsigned long)(now - lastUpdate) < 3000;
  if (fresh) {
    // Left dot pulses while fresh; right bar is mean-removed RMS, 0.25 g full scale.
    frame[3 * 13 + 1] = (now / 500) % 2 ? 7 : 2;
    frame[4 * 13 + 1] = frame[3 * 13 + 1];
    for (int y = 7; y >= 8 - vibrationLevel; --y) {
      for (int x = 4; x < 13; ++x) frame[y * 13 + x] = 5;
    }
  } else {
    // Hollow square: waiting for real samples, or gateway heartbeat expired.
    const uint8_t brightness = (now / 700) % 2 ? 4 : 1;
    for (int x = 4; x <= 8; ++x) {
      frame[1 * 13 + x] = brightness;
      frame[6 * 13 + x] = brightness;
    }
    for (int y = 1; y <= 6; ++y) {
      frame[y * 13 + 4] = brightness;
      frame[y * 13 + 8] = brightness;
    }
  }
  matrix.draw(frame);
  delay(40);
}
