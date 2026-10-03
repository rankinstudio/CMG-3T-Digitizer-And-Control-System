#!/usr/bin/env python3
"""Raw noise capture, one ADS1220 at a time, for PSDs on the PC.

    python3 ads_noise.py SECONDS [short|open] [Z N E]  > file.csv

short: MUX 1110, the converter's inputs shorted internally at mid-supply --
       the ADC's own floor, no wiring needed.
open:  MUX 0000, AIN0/AIN1 through the board's dividers (normal config).
Output: channel,mode,t_seconds,counts  (t from time.monotonic at DRDY).
"""
import sys, time
import spidev
import RPi.GPIO as GPIO

CH = {'Z': (18, 13), 'N': (22, 15), 'E': (29, 31)}
REG0 = {'short': 0xE1, 'open': 0x01}
REG1 = 0x54   # turbo 180 SPS, continuous (the station setting)

def main():
    secs = float(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else 'open'
    names = [a.upper() for a in sys.argv[3:]] or list(CH)
    GPIO.setwarnings(False); GPIO.setmode(GPIO.BOARD)
    for cs, dr in CH.values():
        GPIO.setup(cs, GPIO.OUT, initial=1); GPIO.setup(dr, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    spi = spidev.SpiDev(); spi.open(0, 0); spi.mode = 1; spi.max_speed_hz = 1_000_000
    def x(cs, d):
        GPIO.output(cs, 0); o = spi.xfer2(list(d)); GPIO.output(cs, 1); return o
    print('channel,mode,t,counts')
    try:
        for n in names:
            cs, dr = CH[n]
            x(cs, [0x06]); time.sleep(0.002)
            x(cs, [0x43, REG0[mode], REG1, 0x00, 0x00])
            x(cs, [0x08])
            t_end = time.monotonic() + secs
            out = []
            while time.monotonic() < t_end:
                while GPIO.input(dr):
                    pass
                t = time.monotonic()
                b = x(cs, [0x10, 0, 0, 0])[1:]
                v = (b[0] << 16) | (b[1] << 8) | b[2]
                out.append('%s,%s,%.6f,%d' % (n, mode, t, v - (1 << 24) if v & 0x800000 else v))
            x(cs, [0x06])
            sys.stdout.write('\n'.join(out) + '\n'); sys.stdout.flush()
    finally:
        spi.close(); GPIO.cleanup()

if __name__ == '__main__':
    main()
