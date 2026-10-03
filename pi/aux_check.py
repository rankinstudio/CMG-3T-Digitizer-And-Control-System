#!/usr/bin/env python3
"""CMG-3T AUX board bring-up: ADS1115 mass-position inputs and the three
optocouplers. Run on the station Pi, sensor NOT connected.

    python3 aux_check.py             # ADS1115: address, all four inputs
    python3 aux_check.py opto LOCK 20   # drive one optocoupler for 20 s

ADS1115 (0x48, ADDR to GND), single-ended, FSR +-4.096 V, 128 SPS:
    A0 = Z mass, A1 = N mass, A2 = E mass   (90.9k from the sensor, 10k to VREF)
    A3 = VREF                               (jumper, so VREF is measured, not assumed)
With the mass inputs open no current flows in the 90.9k, so A0-A2 must equal A3.
Generic 10-pin board, upright in D3-D12; A0-A3 reach the dividers through
jumpers 10-13 (E9->F7, E10->G6, E11->H5, E12->I4). Clone boards sometimes
carry an ADS1015 (12-bit): its codes always end in four zero bits, and the
check below fails on that.
Mass voltage once connected: Vmass = (Vin - 0.9009 * VREF) / 0.0991.

Optocouplers (LTV-817, LED from the GPIO through 1k):
    CENTRE GPIO16 (pin 36)  collector A20
    UNLOCK GPIO20 (pin 38)  collector A23
    LOCK   GPIO21 (pin 40)  collector A26      emitter / Y = A19 for all three
Meter on ohms (2k range), red on the collector, black on A19: open with the
GPIO low, a few hundred ohms while it is driven high.
"""
import sys, time

ADDR = 0x48
OPTO = {'CENTRE': 16, 'UNLOCK': 20, 'LOCK': 21}
HOLE = {'CENTRE': 'A20', 'UNLOCK': 'A23', 'LOCK': 'A26'}


def ads_read(bus, ch, raw=False):
    # config: OS=1 single shot, MUX=100+ch (AINch vs GND), PGA=001 (+-4.096 V),
    # MODE=1 single shot, DR=100 (128 SPS), comparator off
    cfg = 0x8000 | ((4 + ch) << 12) | (0b001 << 9) | 0x0100 | (0b100 << 5) | 0x0003
    bus.write_i2c_block_data(ADDR, 0x01, [cfg >> 8, cfg & 0xFF])
    time.sleep(0.012)
    for _ in range(50):
        hi, lo = bus.read_i2c_block_data(ADDR, 0x01, 2)
        if hi & 0x80:            # conversion done
            break
        time.sleep(0.002)
    hi, lo = bus.read_i2c_block_data(ADDR, 0x00, 2)
    v = (hi << 8) | lo
    if v & 0x8000:
        v -= 1 << 16
    return v if raw else v * 4.096 / 32768


I2C_LOCK = '/tmp/cmg3t-ads1115.lock'


def read_all(bus, avg=1):
    """[A0, A1, A2, A3] in volts, under a file lock: cmgcapture.py and cmgctl.py
    both drive this chip, and a single-shot read is configure-then-fetch, so two
    processes interleaving would swap channels."""
    import fcntl
    with open(I2C_LOCK, 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        return [sum(ads_read(bus, ch) for _ in range(avg)) / avg for ch in range(4)]


def check_ads():
    from smbus2 import SMBus
    with SMBus(1) as bus:
        try:
            bus.read_byte_data(ADDR, 0x01)
        except OSError:
            print('ADS1115 not answering at 0x48: check SDA A6 / SCL A5 / VDD A3 / GND jumper A4, '
                  'and ADDR (A7) to the bottom GND rail')
            return 1
        print('ADS1115 found at 0x48')
        codes = [[ads_read(bus, ch, raw=True) for _ in range(8)] for ch in range(4)]
        vals = [sum(c) / len(c) * 4.096 / 32768 for c in codes]
        if all(c & 0xF == 0 for cc in codes for c in cc):
            print('FAIL: all 32 codes end in 0000 -- this chip is an ADS1015 (12-bit), not an ADS1115. '
                  'Check the marking: BOGI = ADS1115, BRPI = ADS1015')
            return 1
        vref = vals[3]
        names = ['A0 Z mass', 'A1 N mass', 'A2 E mass', 'A3 VREF']
        ok = 2.40 < vref < 2.56
        for n, v in zip(names, vals):
            print('   %-10s %.4f V' % (n, v))
        print('VREF on A3: %.4f V  %s' % (vref, 'OK' if ok else 'FAIL (expect ~2.46; is the ADC board powered and the VREF wire in?)'))
        for n, v in zip(names[:3], vals[:3]):
            d = v - vref
            good = abs(d) < 0.010
            ok &= good
            print('   %s - VREF = %+.1f mV  %s' % (n, d * 1e3, 'OK' if good else
                  'FAIL: an open input should sit at VREF -- check its jumper (10-13) across the channel, '
                  'its Rr (J-row to VREF rail) and the strip'))
        print('RESULT:', 'PASS' if ok else 'FAIL')
        return 0 if ok else 1


def drive_opto(name, secs):
    import RPi.GPIO as GPIO
    pin = OPTO[name]
    GPIO.setwarnings(False); GPIO.setmode(GPIO.BCM)
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    try:
        print('%s: GPIO%d HIGH for %d s -- meter %s to A19 now (ohms, 2k range, red on %s)'
              % (name, pin, secs, HOLE[name], HOLE[name]))
        GPIO.output(pin, GPIO.HIGH)
        time.sleep(secs)
    finally:
        GPIO.output(pin, GPIO.LOW)
        GPIO.cleanup(pin)
        print('%s: GPIO%d LOW again -- the reading should go back to open' % (name, pin))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'opto':
        name = sys.argv[2].upper()
        if name not in OPTO:
            sys.exit('use CENTRE, UNLOCK or LOCK')
        drive_opto(name, int(sys.argv[3]) if len(sys.argv) > 3 else 20)
    else:
        sys.exit(check_ads())
