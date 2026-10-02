// ASCII/pixel music visualiser for ESP32 + SSD1306 OLED.
//
// The ESP32 pairs as a Bluetooth A2DP sink (it shows up like a wireless
// speaker). It taps the live PCM audio stream for a real-time spectrum
// visualiser, reads AVRCP "now playing" metadata (title/artist) when the
// connected phone provides it, and -- if you've wired an I2S DAC/amp
// (e.g. MAX98357A) -- plays the audio out loud at the same time.
//
// Hardware:
//   ESP32 DevKit (original ESP32 -- needs classic Bluetooth, so NOT
//                 S2/S3/C3/C6)
//   SSD1306 128x64 OLED over I2C   -> SDA=21, SCL=22 (ESP32 defaults)
//   Optional MAX98357A I2S amp     -> BCLK=26, LRC=25, DIN=27
//
// If you skip the I2S amp, the visualiser still works, but once your
// phone connects to this device as an audio sink it will stop playing
// sound anywhere else -- you'll see it but not hear it. Wire the amp if
// you want both.

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <arduinoFFT.h>
#include "BluetoothA2DPSink.h"

// ---------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------
#define BT_DEVICE_NAME "ASCII Visualizer"

#define OLED_SDA 21
#define OLED_SCL 22
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define OLED_ADDRESS 0x3C

#define ENABLE_I2S_AUDIO_OUT 1  // set to 0 for visual-only, no sound out
#define I2S_BCLK_PIN 26
#define I2S_LRC_PIN 25
#define I2S_DOUT_PIN 27

#define SAMPLE_RATE 44100
#define FFT_SAMPLES 512  // power of two
#define RING_SIZE 4096   // must be >= FFT_SAMPLES
#define NUM_BARS 16
#define BAR_AREA_TOP 30
#define BAR_AREA_BOTTOM 63
#define BAR_QUANTUM_PX 3  // chunky "pixel block" steps, retro look

#define VISUALIZER_FPS 20
#define SCROLL_PERIOD_MS 90

// ---------------------------------------------------------------------
// Globals
// ---------------------------------------------------------------------
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
BluetoothA2DPSink a2dp_sink;

static SemaphoreHandle_t ringMutex;
static int16_t ringBuffer[RING_SIZE];
static volatile uint32_t ringWritePos = 0;
static volatile uint32_t ringFilled = 0;

static SemaphoreHandle_t metaMutex;
static char titleBuf[64] = "";
static char artistBuf[64] = "";
static volatile bool metaDirty = true;

static float vReal[FFT_SAMPLES];
static float vImag[FFT_SAMPLES];
ArduinoFFT<float> FFT(vReal, vImag, FFT_SAMPLES, (float)SAMPLE_RATE);

static float smoothedBars[NUM_BARS] = {0};
static float runningMax = 1.0f;
static int bandStartBin[NUM_BARS + 1];

static char marqueeText[140] = "Waiting for Bluetooth connection...";
static int marqueeTextWidth = 0;
static int marqueeOffset = 0;
static unsigned long lastScrollMs = 0;
static unsigned long lastFrameMs = 0;

// ---------------------------------------------------------------------
// Bluetooth audio stream tap: runs on the A2DP task, must stay fast.
// ---------------------------------------------------------------------
void audio_data_callback(const uint8_t *data, uint32_t length) {
  const int16_t *samples = reinterpret_cast<const int16_t *>(data);
  uint32_t frame_count = length / 4;  // interleaved stereo, 16-bit

  if (xSemaphoreTake(ringMutex, 0) != pdTRUE) {
    return;  // visualiser will just miss this chunk; audio is unaffected
  }
  for (uint32_t i = 0; i < frame_count; i++) {
    int16_t l = samples[2 * i];
    int16_t r = samples[2 * i + 1];
    int16_t mono = (int16_t)(((int32_t)l + (int32_t)r) / 2);
    ringBuffer[ringWritePos] = mono;
    ringWritePos = (ringWritePos + 1) % RING_SIZE;
  }
  ringFilled = min((uint32_t)RING_SIZE, ringFilled + frame_count);
  xSemaphoreGive(ringMutex);
}

// ---------------------------------------------------------------------
// AVRCP "now playing" metadata: runs on a BT task, must stay fast too.
// ---------------------------------------------------------------------
void avrc_metadata_callback(uint8_t id, const uint8_t *text) {
  if (xSemaphoreTake(metaMutex, pdMS_TO_TICKS(20)) != pdTRUE) {
    return;
  }
  switch (id) {
    case ESP_AVRC_MD_ATTR_TITLE:
      strncpy(titleBuf, (const char *)text, sizeof(titleBuf) - 1);
      titleBuf[sizeof(titleBuf) - 1] = 0;
      metaDirty = true;
      break;
    case ESP_AVRC_MD_ATTR_ARTIST:
      strncpy(artistBuf, (const char *)text, sizeof(artistBuf) - 1);
      artistBuf[sizeof(artistBuf) - 1] = 0;
      metaDirty = true;
      break;
    default:
      break;
  }
  xSemaphoreGive(metaMutex);
}

// ---------------------------------------------------------------------
// Spectrum: log-spaced bin bucketing precomputed once in setup().
// ---------------------------------------------------------------------
void computeBandEdges() {
  const float minFreq = 60.0f;
  const float maxFreq = min(16000.0f, SAMPLE_RATE / 2.0f * 0.9f);
  const float binHz = (float)SAMPLE_RATE / (float)FFT_SAMPLES;

  for (int i = 0; i <= NUM_BARS; i++) {
    float t = (float)i / (float)NUM_BARS;
    float freq = minFreq * powf(maxFreq / minFreq, t);
    int bin = (int)(freq / binHz);
    bandStartBin[i] = constrain(bin, 1, FFT_SAMPLES / 2 - 1);
  }
}

void updateSpectrum() {
  static int16_t snapshot[FFT_SAMPLES];

  if (xSemaphoreTake(ringMutex, portMAX_DELAY) == pdTRUE) {
    uint32_t pos = ringWritePos;
    uint32_t filled = ringFilled;
    xSemaphoreGive(ringMutex);

    if (filled < FFT_SAMPLES) {
      for (int i = 0; i < NUM_BARS; i++) smoothedBars[i] *= 0.85f;
      return;
    }
    for (int i = 0; i < FFT_SAMPLES; i++) {
      int32_t idx = ((int32_t)pos - FFT_SAMPLES + i + RING_SIZE) % RING_SIZE;
      snapshot[i] = ringBuffer[idx];
    }
  }

  for (int i = 0; i < FFT_SAMPLES; i++) {
    vReal[i] = (float)snapshot[i];
    vImag[i] = 0.0f;
  }

  FFT.windowing(FFTWindow::Hamming, FFTDirection::Forward);
  FFT.compute(FFTDirection::Forward);
  FFT.complexToMagnitude();

  float rawLevels[NUM_BARS];
  float peak = 0.0f;
  for (int i = 0; i < NUM_BARS; i++) {
    int lo = bandStartBin[i];
    int hi = max(bandStartBin[i + 1], lo + 1);
    float sum = 0.0f;
    for (int b = lo; b < hi; b++) sum += vReal[b];
    float level = logf(1.0f + (sum / (hi - lo)) * 0.5f);
    rawLevels[i] = level;
    if (level > peak) peak = level;
  }

  runningMax = max(peak, runningMax * 0.995f);
  if (runningMax < 1.0f) runningMax = 1.0f;

  for (int i = 0; i < NUM_BARS; i++) {
    float normalized = rawLevels[i] / runningMax;
    normalized = constrain(normalized, 0.0f, 1.0f);
    float rate = (normalized > smoothedBars[i]) ? 0.6f : 0.18f;
    smoothedBars[i] += (normalized - smoothedBars[i]) * rate;
  }
}

// ---------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------
void drawIdleScreen() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 8);
  display.println("ASCII Visualizer");
  display.setCursor(0, 22);
  display.println("Pair via Bluetooth to:");
  display.setCursor(0, 34);
  display.println(BT_DEVICE_NAME);
  display.setCursor(0, 50);
  display.println("Waiting for connection...");
  display.display();
}

void drawSpectrum() {
  const int areaHeight = BAR_AREA_BOTTOM - BAR_AREA_TOP;
  const int gap = 2;
  const int barWidth = (SCREEN_WIDTH - gap * (NUM_BARS - 1)) / NUM_BARS;

  for (int i = 0; i < NUM_BARS; i++) {
    int rawHeight = (int)(smoothedBars[i] * areaHeight);
    int quantized = (rawHeight / BAR_QUANTUM_PX) * BAR_QUANTUM_PX;
    if (rawHeight > 0 && quantized == 0) quantized = BAR_QUANTUM_PX;
    quantized = constrain(quantized, 0, areaHeight);

    int x = i * (barWidth + gap);
    int y = BAR_AREA_BOTTOM - quantized;
    if (quantized > 0) {
      display.fillRect(x, y, barWidth, quantized, SSD1306_WHITE);
    }
  }
}

void updateMarqueeText() {
  if (xSemaphoreTake(metaMutex, pdMS_TO_TICKS(20)) == pdTRUE) {
    if (metaDirty) {
      if (strlen(titleBuf) == 0) {
        strcpy(marqueeText, "Bluetooth Audio    ");
      } else if (strlen(artistBuf) == 0) {
        snprintf(marqueeText, sizeof(marqueeText), "%s    ", titleBuf);
      } else {
        snprintf(marqueeText, sizeof(marqueeText), "%s \xf7 %s    ", titleBuf, artistBuf);
      }
      int16_t x1, y1;
      uint16_t w, h;
      display.setTextSize(1);
      display.getTextBounds(marqueeText, 0, 0, &x1, &y1, &w, &h);
      marqueeTextWidth = w;
      marqueeOffset = 0;
      metaDirty = false;
    }
    xSemaphoreGive(metaMutex);
  }
}

void drawMarquee() {
  if (marqueeTextWidth <= SCREEN_WIDTH) {
    display.setCursor(0, 0);
    display.print(marqueeText);
    return;
  }
  unsigned long now = millis();
  if (now - lastScrollMs >= SCROLL_PERIOD_MS) {
    lastScrollMs = now;
    marqueeOffset++;
    if (marqueeOffset > marqueeTextWidth) marqueeOffset = 0;
  }
  display.setCursor(-marqueeOffset, 0);
  display.print(marqueeText);
  display.setCursor(-marqueeOffset + marqueeTextWidth, 0);
  display.print(marqueeText);
}

void drawConnectedScreen() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  drawMarquee();
  display.drawFastHLine(0, 12, SCREEN_WIDTH, SSD1306_WHITE);
  drawSpectrum();
  display.display();
}

// ---------------------------------------------------------------------
// Arduino entry points
// ---------------------------------------------------------------------
void setup() {
  Serial.begin(115200);

  ringMutex = xSemaphoreCreateMutex();
  metaMutex = xSemaphoreCreateMutex();

  Wire.begin(OLED_SDA, OLED_SCL);
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS)) {
    Serial.println("SSD1306 init failed -- check wiring/address");
  }
  display.setTextColor(SSD1306_WHITE);
  drawIdleScreen();

  computeBandEdges();

#if ENABLE_I2S_AUDIO_OUT
  i2s_pin_config_t pin_config = {
      .bck_io_num = I2S_BCLK_PIN,
      .ws_io_num = I2S_LRC_PIN,
      .data_out_num = I2S_DOUT_PIN,
      .data_in_num = I2S_PIN_NO_CHANGE,
  };
  a2dp_sink.set_pin_config(pin_config);
#endif

  a2dp_sink.set_stream_reader(audio_data_callback, ENABLE_I2S_AUDIO_OUT);
  a2dp_sink.set_avrc_metadata_callback(avrc_metadata_callback);
  a2dp_sink.start(BT_DEVICE_NAME);

  Serial.println("A2DP sink started, waiting for a phone to connect...");
}

static bool lastConnected = false;

void loop() {
  bool connected = a2dp_sink.is_connected();

  if (connected != lastConnected) {
    lastConnected = connected;
    if (!connected) {
      drawIdleScreen();  // just disconnected: revert from the visualiser
    }
  }

  if (!connected) {
    delay(200);
    return;
  }

  unsigned long now = millis();
  if (now - lastFrameMs < (1000 / VISUALIZER_FPS)) {
    delay(1);
    return;
  }
  lastFrameMs = now;

  updateMarqueeText();
  updateSpectrum();
  drawConnectedScreen();
}
