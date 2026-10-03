#!/usr/bin/env python3
"""CMG-3T mass control and mass positions, from the Pi.

    python3 cmgctl.py mass            # mass positions, once
    python3 cmgctl.py mass 60         # every 2 s for 60 s
    python3 cmgctl.py lock            # LOCK line low for 7 s, then watch 3 min
    python3 cmgctl.py unlock          # UNLOCK, same
    python3 cmgctl.py centre          # CENTRE, same

Each command drives its optocoupler (GPIO21 lock, GPIO20 unlock, GPIO16
centre; active-low lines pulled to Y) for --secs (default 7), then logs the
mass positions so you can see the sensor's own motor sequence finish. That
takes a few minutes and is audible; it is normal.

Runs alongside cmgcapture.py: the capture never touches these GPIOs, and
both only read the ADS1115, so sharing the I2C bus is fine.

Unlock or centre only with the sensor level on its pad. Never move it unlocked.
"""
import argparse
import time

from smbus2 import SMBus

import aux_check

LINES = {'lock': 21, 'unlock': 20, 'centre': 16, 'center': 16}


def mass(bus):
    v = aux_check.read_all(bus, avg=4)
    return [(x - 0.9009 * v[3]) / 0.0991 for x in v[:3]]


def show(bus, t0):
    z, n, e = mass(bus)
    print('t=%5.0f s   Z %+6.2f   N %+6.2f   E %+6.2f V' % (time.time() - t0, z, n, e), flush=True)


def watch(bus, secs, every=2.0):
    t0 = time.time()
    while True:
        show(bus, t0)
        if time.time() - t0 >= secs:
            return
        time.sleep(every)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cmd', choices=['mass', 'lock', 'unlock', 'centre', 'center'])
    ap.add_argument('watch', nargs='?', type=float, default=None,
                    help='seconds to watch afterwards (mass: default once; others: 180)')
    ap.add_argument('--secs', type=float, default=7.0, help='how long to hold the line (default 7)')
    a = ap.parse_args()

    with SMBus(1) as bus:
        if a.cmd == 'mass':
            watch(bus, a.watch or 0)
            return
        import RPi.GPIO as GPIO
        pin = LINES[a.cmd]
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
        t0 = time.time()
        show(bus, t0)
        try:
            print('%s: GPIO%d on for %.0f s' % (a.cmd.upper(), pin, a.secs), flush=True)
            GPIO.output(pin, GPIO.HIGH)
            while time.time() - t0 < a.secs:
                time.sleep(1)
                show(bus, t0)
        finally:
            GPIO.output(pin, GPIO.LOW)
            GPIO.cleanup(pin)
        print('released', flush=True)
        watch(bus, 180 if a.watch is None else a.watch, every=5.0)


if __name__ == '__main__':
    main()
