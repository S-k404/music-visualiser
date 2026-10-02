# ASCII/Pixel Music Visualiser (ESP32 + SSD1306)

The ESP32 pairs over Bluetooth as an **A2DP sink** — it shows up on your
phone like a wireless speaker. It taps the live digital audio stream to
drive a real-time spectrum visualiser on a small SSD1306 OLED, and shows
the track's title/artist when your phone sends that over AVRCP.

```
Phone ──Bluetooth A2DP──▶ ESP32
                            │
                 ┌──────────┼──────────┐
                 ▼          ▼          ▼
              FFT/PCM   SSD1306    I2S DAC/amp
              spectrum   title/artist  (optional
                         + bars         audio out)
```

## ⚠️ Important: you won't hear anything unless you wire the amp

Once your phone connects to the ESP32 as its Bluetooth audio output, **all
audio goes to the ESP32** — your phone's own speaker stops playing it. If
you don't wire up the optional I2S amp below, the visualiser still works
perfectly (you'll see the spectrum react), but you'll hear nothing. Wire
the MAX98357A if you want to both see and hear it.

## Bill of materials

| Part | Notes |
|---|---|
| ESP32 DevKit (original ESP32) | **Must** support classic Bluetooth — do **not** use ESP32-S2/S3/C3/C6, they only have BLE, no A2DP. An "ESP32-WROOM-32" dev board is the safe choice. |
| SSD1306 OLED, 128x64, I2C | The cheap 0.96" ones. Needs 4 wires: VCC, GND, SDA, SCL. |
| MAX98357A I2S amp + small 4/8Ω speaker | *Optional* — only needed if you want audible output. |

## Wiring

**OLED (I2C):**

| OLED pin | ESP32 pin |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SDA | GPIO 21 |
| SCL | GPIO 22 |

**MAX98357A amp (optional, only if you want sound):**

| Amp pin | ESP32 pin |
|---|---|
| VIN | 5V |
| GND | GND |
| BCLK | GPIO 26 |
| LRC | GPIO 25 |
| DIN | GPIO 27 |

If your OLED's I2C address isn't `0x3C` (some clones use `0x3D`), change
`OLED_ADDRESS` at the top of `src/main.cpp`. All pins above are `#define`s
at the top of that file if you need to move them.

## Build & flash

This is a [PlatformIO](https://platformio.org/) project — it was built and
verified to compile cleanly against the real library versions before being
handed to you (see below).

```bash
cd esp32
./build.sh            # first run installs PlatformIO into esp32/.venv-pio
./build.sh upload     # flash it (board connected via USB)
./build.sh monitor    # watch serial logs at 115200 baud
```

Or with PlatformIO already installed / via the PlatformIO IDE extension in
VS Code: just open this `esp32/` folder, it'll pick up `platformio.ini`
automatically.

## Using it

1. Flash the board, power it up. The OLED shows "Waiting for
   connection..." and the device name `ASCII Visualizer`.
2. On your phone: Bluetooth settings → pair with `ASCII Visualizer`.
3. Play music. The spectrum bars should start reacting within a second or
   two, and the title/artist marquee updates when your phone sends it.

### Controls

There's no buttons in this build — it's designed to just sit there and
react. If you want physical controls (e.g. a button to cycle visual
modes), that's a natural next step; say the word and it can be added.

### Track metadata caveat

AVRCP metadata (title/artist) support is entirely up to your phone/OS and
the app you're playing from — Android + most music apps send it reliably;
iOS support varies by app and iOS version, and some apps send nothing at
all. If no metadata ever arrives, the marquee just shows "Bluetooth
Audio" and the spectrum still works fine.

## Technical notes

- **Audio tap**: `BluetoothA2DPSink::set_stream_reader()` gives a callback
  with raw interleaved 16-bit stereo PCM as it's decoded; it's mixed to
  mono and pushed into a small ring buffer guarded by a FreeRTOS mutex.
  The main loop pulls a 512-sample window from that ring buffer at ~20fps
  for the FFT (`arduinoFFT`), buckets it into 16 log-spaced frequency
  bands (60 Hz–16 kHz), and renders them as chunky, quantized "pixel
  block" bars for a retro look.
- **I2S passthrough**: `set_stream_reader(callback, true)` — the `true`
  keeps the library's own I2S audio output running alongside our tap, so
  tapping for visualisation doesn't affect playback.
- **Metadata**: `set_avrc_metadata_callback()` fires on title/artist
  changes; written into fixed-size `char[]` buffers (not `String`) since
  it's called from a different FreeRTOS task than `loop()` and `String`
  isn't safe to mutate across tasks.
- You'll see a harmless compiler warning: `"AudioTools library is not
  included first or installed"`. That's the ESP32-A2DP library telling you
  it's falling back to its built-in I2S output instead of the optional
  AudioTools integration — expected here, not an error.
- Flash partitioning is set to `huge_app.csv` (no OTA) since the default
  scheme left the build at ~89% of a small app partition; this gives ~3MB
  of headroom instead.

## Verified

`pio run` was run against the real registry/GitHub library versions
(ESP32-A2DP, Adafruit SSD1306/GFX/BusIO, arduinoFFT) and compiles cleanly.
What has **not** been verified is real hardware — it hasn't been flashed
to a physical ESP32 or paired with a phone, since that needs the board in
hand. If `pio run --target upload` or the on-device behaviour turns up
issues, bring them back and they can be fixed directly.
