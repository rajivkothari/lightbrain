# LightBrain Hardware Validation Log

Fill this in on hardware day.  One entry per test session.
Scripts print timestamped output — pipe to a `.log` file and attach here by reference.

---

## Session template

Copy this block for each hardware session.

```
Date:        YYYY-MM-DD
Operator:    
Location:    
Adapter:     DMXking ultraDMX MAX / Enttec USB Pro / other: ___
Serial port: /dev/ttyUSB0 / COM__ / other: ___
Cable:       direct / wireless DMX (brand: ___)
Git commit:  (run: git rev-parse --short HEAD)
Log files:   (filenames of any captured .log files)
```

---

## Stage 1 — Adapter detection (`hw_detect.py`)

**Script:** `python scripts/hw_detect.py --port <PORT>`

| Check | Expected | Observed | Pass/Fail |
|-------|----------|----------|-----------|
| Port enumerated by OS | `/dev/ttyUSB0` or `COM*` in list | | |
| FTDI VID/PID detected | `0403:6001` or similar | | |
| Serial port opens | No error | | |
| Blackout frame transmits | No exception | | |
| Fixture dark after blackout | All LEDs off | | |

**Notes / deviations:**

```
(none)
```

---

## Stage 2 — Wash FX2 Left (address 1) — direct cable

**Script:** `python scripts/hw_wash_fx2.py --port <PORT> --address 1 2>&1 | tee washfx2_addr1_direct.log`

| Step | Expected | Observed | Pass/Fail | Notes |
|------|----------|----------|-----------|-------|
| Blackout | All zones dark | | | |
| Red full (Ch1=255) | All 6 zones solid red | | | |
| Green full (Ch2=255) | All 6 zones solid green | | | |
| Blue full (Ch3=255) | All 6 zones solid blue | | | |
| UV full (Ch4=255) | UV LEDs lit (blue-purple) | | | |
| White RGB (R+G+B=255) | Bright white | | | |
| Warm white (hue≈30, sat≈0.35) | Warmer white vs pure RGB | | | |
| Strobe mid (Ch5≈128) | ~10 Hz strobe | | | |
| Strobe off → blackout | Dark after strobe stops | | | |
| Dimmer ramp (red, 0→1→0) | Smooth fade, no flicker | | | |

**Channel map verification:**

Did any channel produce unexpected output?

```
Ch 1 (Red):    [ ] correct  [ ] wrong — observed: ___
Ch 2 (Green):  [ ] correct  [ ] wrong — observed: ___
Ch 3 (Blue):   [ ] correct  [ ] wrong — observed: ___
Ch 4 (UV):     [ ] correct  [ ] wrong — observed: ___
Ch 5 (Strobe): [ ] correct  [ ] wrong — observed: ___
Ch 6 (Auto):   [ ] no response (correct)  [ ] unexpected response: ___
Ch 7 (Speed):  [ ] no response (correct)  [ ] unexpected response: ___
Ch 8 (Dimmer): [ ] no response in manual (correct)  [ ] unexpected: ___
```

**Notes / deviations:**

```
(none)
```

---

## Stage 3 — Wash FX2 Right (address 9) — direct cable

**Script:** `python scripts/hw_wash_fx2.py --port <PORT> --address 9 2>&1 | tee washfx2_addr9_direct.log`

*(Same table as Stage 2 — copy and fill in)*

**Channel map verification:**

```
(same fields as above)
```

**Notes / deviations:**

```
(none)
```

---

## Stage 4 — GigBAR Move+ILS (address 17) — direct cable

**Script:** `python scripts/hw_gigbar.py --port <PORT> --address 17 2>&1 | tee gigbar_addr17_direct.log`

| Step | Expected | Observed | Pass/Fail | Notes |
|------|----------|----------|-----------|-------|
| Blackout | All sections dark | | | |
| Par Red (Ch1=255) | Par wash solid red | | | |
| Par White+Amber (Ch4+5) | Warm white wash | | | |
| Par UV (Ch6=255) | UV glow | | | |
| Derby rotation (Ch13=64) | Derby spinning CW with beams | | | |
| Flash LEDs (Ch14-17=128) | 4 small white LEDs lit | | | |
| Spot centre aim | Spot head at centre, beam visible | | | |
| Spot pan sweep | Beam sweeps L→R smoothly | | | |
| Laser (opt-in) | Laser patterns visible | | | |
| Par strobe mid | Par wash strobing ~10 Hz | | | |

**Section channel map verification:**

```
Par (Ch1-7):
  Ch 1 Par Red:      [ ] correct  [ ] wrong: ___
  Ch 2 Par Green:    [ ] correct  [ ] wrong: ___
  Ch 3 Par Blue:     [ ] correct  [ ] wrong: ___
  Ch 4 Par Amber:    [ ] correct  [ ] wrong: ___
  Ch 5 Par White:    [ ] correct  [ ] wrong: ___
  Ch 6 Par UV:       [ ] correct  [ ] wrong: ___
  Ch 7 Par Strobe:   [ ] correct  [ ] wrong: ___

Derby (Ch8-13):
  Ch  8 Derby Red:      [ ] correct  [ ] wrong: ___
  Ch  9 Derby Green:    [ ] correct  [ ] wrong: ___
  Ch 10 Derby Blue:     [ ] correct  [ ] wrong: ___
  Ch 11 Derby White:    [ ] correct  [ ] wrong: ___
  Ch 12 Derby Strobe:   [ ] correct  [ ] wrong: ___
  Ch 13 Derby Rotation: [ ] correct  [ ] wrong: ___

Flash LEDs (Ch14-18):
  Ch 14-17 Flash LEDs:  [ ] correct  [ ] wrong: ___
  Ch 18 Flash Strobe:   [ ] correct  [ ] wrong: ___

Laser (Ch19-20):
  Ch 19 Laser control:  [ ] correct  [ ] wrong: ___
  Ch 20 Laser patterns: [ ] correct  [ ] wrong: ___

Spot head (Ch21-29):
  Ch 21 Pan:         [ ] correct  [ ] wrong: ___
  Ch 22 Fine Pan:    [ ] correct  [ ] wrong: ___
  Ch 23 Tilt:        [ ] correct  [ ] wrong: ___
  Ch 24 Fine Tilt:   [ ] correct  [ ] wrong: ___
  Ch 25 P/T Speed:   [ ] correct  [ ] wrong: ___
  Ch 26 Color Wheel: [ ] correct  [ ] wrong: ___
  Ch 27 Gobo Wheel:  [ ] correct  [ ] wrong: ___
  Ch 28 Spot Dimmer: [ ] correct  [ ] wrong: ___
  Ch 29 Spot Strobe: [ ] correct  [ ] wrong: ___
```

**Notes / deviations:**

```
(none)
```

---

## Stage 5 — Wireless DMX repeat

Repeat Stages 2–4 with the W-DMX / wireless DMX relay in the chain.
Document any differences vs direct cable.

**Wireless DMX hardware:**

```
Brand/model:  ___
Transmitter:  ___
Receiver:     ___
Pairing status before test:  ___
```

| Fixture | Direct cable result | Wireless result | Delta |
|---------|---------------------|-----------------|-------|
| Wash FX2 Left (addr 1) | | | |
| Wash FX2 Right (addr 9) | | | |
| GigBAR (addr 17) | | | |

**Wireless-specific issues:**

```
(none)
```

---

## Stage 6 — Full pipeline test

**Command:**
```bash
python -m app.main --demo --serial <PORT> --web
# Open http://localhost:8765/ in browser
```

| Test | Expected | Observed | Pass/Fail | Notes |
|------|----------|----------|-----------|-------|
| Engine starts, dashboard loads | No crash, 590 tests in background | | | |
| Mode: open_dance → fixtures show color | Room wash lights up | | | |
| Mode: dinner → fixtures dim, warm | Lower intensity, warm palette | | | |
| Mode: banger → auto strobe on beat | Strobe fires on high-energy frames | | | |
| BLACKOUT button | All fixtures instantly dark | | | |
| BLACKOUT release | 1.5 s fade-up from dark | | | |
| Kill STROBE | Strobe stops; room wash continues | | | |
| Kill DERBY | Derby stops; other sections continue | | | |
| Kill LASER | Laser off; GigBAR pars continue | | | |
| Master dimmer 0% | All fixtures dark (not blackout) | | | |
| Master dimmer 100% | Fixtures at full configured brightness | | | |
| Uplight dimmer 50% | Wash FX2 at 50%, GigBAR pars unchanged | | | |

**Notes / deviations:**

```
(none)
```

---

## Known mapper limitations (pre-hardware)

These are documented gaps between the mapper and expected real-world behavior.
Update during hardware validation with confirmed behavior.

| Fixture | Issue | Status |
|---------|-------|--------|
| Wash FX2 | Ch8 Dimmer is inactive in manual colour mode (Ch6=0). Brightness controlled by scaling R/G/B directly. | Expected — no fix needed |
| Wash FX2 | No White or Amber channel. `white` and `amber` render params are silently ignored. | Expected — no fix needed |
| GigBAR | Scene group overrides apply to visualizer only; DMX routing to individual fixture groups is not implemented. | Known TODO |
| GigBAR | Spot gobo wheel always set to open (position 0). No gobo selection in current mapper. | Known — intentional for now |
| GigBAR | Flash LEDs track room brightness at a fixed fraction; no independent control from dashboard. | Known — intentional for now |

---

## Discovered mismatches

*Fill in any channel map mismatches found during validation.*

| Date | Fixture | Channel | Documented behavior | Actual behavior | Action taken |
|------|---------|---------|--------------------|--------------------|--------------|
| | | | | | |

---

## Sign-off

```
Hardware validation complete:  [ ] yes  [ ] partial  [ ] not yet
Sign-off date:   ___
Operator:        ___
Open issues:     ___
Next steps:      ___
```
