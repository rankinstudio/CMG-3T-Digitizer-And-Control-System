#!/usr/bin/env python3
"""CMG-3T ADC board bring-up check: three ADS1220s on SPI0, CS and DRDY by hand.

Run on the station Pi with the ADC board powered and the sensor NOT connected:

    python3 ads_check.py            # all three
    python3 ads_check.py Z          # one channel

Per module, in order:
  1. CS isolation -- each module gets a different reg0 test value, then each is
     read back. A module answering with a neighbour's value means two CS lines
     are crossed or bridged; all 0xFF / 0x00 means MISO or that CS is open.
  2. Register write and readback of the real config (0x01 0x54 0x00 0x00).
  3. START, then time DRDY for a few seconds: should be ~180 SPS (turbo).
  4. Open-input noise. With the sensor leads open both inputs sit at VREF
     through matched 10k legs, so the reading is ~0 counts plus converter noise.

Pins (see cmg3t-digitizer.html, Raspberry Pi section):
    SCLK 23, MOSI 19, MISO 21 shared
    CS   Z 18 (GPIO24)   N 22 (GPIO25)   E 29 (GPIO5)
    DRDY Z 13 (GPIO27)   N 15 (GPIO22)   E 31 (GPIO6)
"""

import statistics
import sys
import time

import spidev
import RPi.GPIO as GPIO

RESET_CMD, START_CMD, RDATA = 0x06, 0x08, 0x10
RREG, WREG = 0x20, 0x40
CONFIG = (0x01, 0x54, 0x00, 0x00)   # AIN0/AIN1, gain 1, PGA bypassed; turbo 180 SPS continuous; internal 2.048 V

CH = {'Z': (18, 13), 'N': (22, 15), 'E': (29, 31)}   # board pin numbers: (CS, DRDY)
TEST = {'Z': 0x11, 'N': 0x21, 'E': 0x31}             # distinct MUX codes, PGA bypass kept

VREF_ADC = 2.048
LSB_V = VREF_ADC / 2**23
SENS_ADC = 1500 * 4.99e3 / (4.99e3 + 10e3)           # V/(m/s) at the converter pins
NM_S_PER_COUNT = LSB_V / SENS_ADC * 1e9


def xfer(spi, cs, data):
    GPIO.output(cs, GPIO.LOW)
    out = spi.xfer2(list(data))
    GPIO.output(cs, GPIO.HIGH)
    return out


def wreg(spi, cs, vals):
    xfer(spi, cs, [WREG | (len(vals) - 1)] + list(vals))


def rreg(spi, cs, n=4):
    return xfer(spi, cs, [RREG | (n - 1)] + [0] * n)[1:]


def hexs(b):
    return ' '.join('0x%02X' % x for x in b)


def main(names):
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD)
    for n in CH:                       # every CS high first, even ones not under test
        GPIO.setup(CH[n][0], GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(CH[n][1], GPIO.IN, pull_up_down=GPIO.PUD_UP)

    spi = spidev.SpiDev()
    spi.open(0, 0)
    try:
        spi.no_cs = True
    except OSError:
        pass                            # Pi 5: CE0 on pin 24 toggles into thin air
    spi.mode = 1
    spi.max_speed_hz = 1_000_000

    ok = {n: True for n in names}
    try:
        for n in names:
            xfer(spi, CH[n][0], [RESET_CMD])
        time.sleep(0.002)

        print('1. CS isolation')
        for n in names:
            wreg(spi, CH[n][0], [TEST[n]])
        for n in names:
            got = rreg(spi, CH[n][0], 1)[0]
            good = got == TEST[n]
            ok[n] &= good
            hint = ''
            if not good:
                other = [m for m in TEST if TEST[m] == got]
                hint = ('  <- answered as %s: CS lines crossed/bridged' % other[0] if other else
                        '  <- nothing answering: MISO, SCLK, 3V3 or this CS open' if got in (0x00, 0xFF) else
                        '  <- garbage: check MOSI / SCLK / seating')
            print('   %s  wrote 0x%02X  read 0x%02X  %s%s' % (n, TEST[n], got, 'OK' if good else 'FAIL', hint))

        print('2. Register readback')
        for n in names:
            xfer(spi, CH[n][0], [RESET_CMD])
            time.sleep(0.002)
            wreg(spi, CH[n][0], CONFIG)
            got = tuple(rreg(spi, CH[n][0]))
            good = got == CONFIG
            ok[n] &= good
            print('   %s  %s  %s' % (n, hexs(got), 'OK' if good else 'FAIL (expected %s)' % hexs(CONFIG)))

        print('3. DRDY rate and 4. open-input noise (5 s)')
        live = [n for n in names if ok[n]]
        for n in live:
            xfer(spi, CH[n][0], [START_CMD])
        data = {n: [] for n in live}
        first = {n: None for n in live}
        last = {n: None for n in live}
        prev = {n: 1 for n in live}
        t_end = time.monotonic() + 5.0
        while time.monotonic() < t_end:
            for n in live:
                cs, drdy = CH[n]
                lvl = GPIO.input(drdy)
                if lvl == 0 and prev[n] == 1:          # falling edge = new sample
                    now = time.monotonic()
                    b = xfer(spi, cs, [RDATA, 0, 0, 0])[1:]
                    v = (b[0] << 16) | (b[1] << 8) | b[2]
                    data[n].append(v - (1 << 24) if v & 0x800000 else v)
                    first[n] = first[n] or now
                    last[n] = now
                prev[n] = lvl
        for n in names:
            if n not in live:
                print('   %s  skipped (failed above)' % n)
                continue
            d = data[n]
            if len(d) < 10:
                ok[n] = False
                print('   %s  only %d DRDY edges in 5 s: FAIL -- DRDY wire (pin %d) open?' % (n, len(d), CH[n][1]))
                continue
            rate = (len(d) - 1) / (last[n] - first[n])
            d = d[5:]                                   # drop the filter-settling samples
            mean, sd = statistics.fmean(d), statistics.pstdev(d)
            rate_ok = 165 < rate < 200
            ok[n] &= rate_ok
            print('   %s  %5.1f SPS %s   mean %+8.1f counts (%+.2f uV)   noise %6.1f counts rms'
                  ' = %.2f uV = %.1f nm/s equivalent'
                  % (n, rate, 'OK' if rate_ok else 'FAIL', mean, mean * LSB_V * 1e6,
                     sd, sd * LSB_V * 1e6, sd * NM_S_PER_COUNT))
    finally:
        for n in CH:
            try:
                xfer(spi, CH[n][0], [RESET_CMD])        # leave them idle
            except Exception:
                pass
        spi.close()
        GPIO.cleanup()

    print('\nRESULT: ' + '  '.join('%s %s' % (n, 'PASS' if ok[n] else 'FAIL') for n in names))
    return 0 if all(ok.values()) else 1


if __name__ == '__main__':
    pick = [a.upper() for a in sys.argv[1:]] or list(CH)
    bad = [p for p in pick if p not in CH]
    if bad:
        sys.exit('unknown channel %s; use Z, N and/or E' % ' '.join(bad))
    sys.exit(main(pick))
