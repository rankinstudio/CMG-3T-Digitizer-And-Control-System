#!/usr/bin/env python3
"""CMG-3T capture daemon: three ADS1220s + the ADS1115 mass positions -> disk + TCP.

    python3 cmgcapture.py            # real hardware
    python3 cmgcapture.py --no-log   # stream only

geocapture.py (the EG-4.5 station's daemon) provides the timing, the miniSEED
writer, the pruning and the network; this file imports those unchanged
(geocapture.py, geostream.py and loadOpts.py sit next to it on the Pi) and adds:

One clock. The three ADS1220s run single-shot: the Pi starts all three at once
on an exact UTC grid (150 SPS), waits until all three have finished, then reads
all three. No SPI traffic ever overlaps a conversion, and the three channels
sample the same instant, timed by the NTP-disciplined system clock.

Why not free-running: three chips on their own oscillators (a few tenths of a
percent apart), each read the moment it is ready, means SPI reads land during
the other chips' conversions and disturb them. Converting together and reading
after keeps the bus quiet while they convert.

One output grid. A band-limited (Kaiser-windowed sinc) resampler takes the
150 SPS grid to the 100 SPS one, t = k / 100 s. The kernel is also the
anti-alias filter: flat to 35 Hz (+-0.05 %), -6 dB at 40, >85 dB down from 45
Hz up (mains and motor hum), zero phase. No DC block and no notch: the 3T is
flat to 120 s and a high-pass here would eat the band the instrument was
bought for. The ADS1220's own sinc3
droop (~-3 dB at 50 Hz) is not corrected.

Disk. Hourly miniSEED per channel, NET.STA..HHZ/HHN/HHE (FLOAT32 nm/s, 100 SPS
exactly), and the mass positions as LMZ/LMN/LME (FLOAT32 volts, 1 SPS).

Network. GEO1 blocks (geostream.py), one port per component: zPort / nPort /
ePort (1244-1246). geoclient.py works on any one of them; cmgclient.py shows
all three.

The lock / unlock / centre lines (GPIO16/20/21) are NOT touched here; use
cmgctl.py.
"""
import argparse
import math
import os
import queue
import signal
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from loadOpts import load_config
from geostream import pack_block
from geocapture import (log, wait_for_ntp, MSeedWriter, CsvWriter,
                        prune_log, Broadcaster, HAVE_OBSPY, STATUS_EVERY)

# ------------------------------------------------------------------ ADS1220 --
RESET_CMD, START_CMD, POWERDOWN, RDATA = 0x06, 0x08, 0x02, 0x10
RREG, WREG = 0x20, 0x40
CONFIG = (0x01, 0x50, 0x00, 0x00)   # AIN0/AIN1, gain 1, PGA bypassed; turbo DR 010 SINGLE-SHOT; internal 2.048 V
FS_IN = 150.0                       # conversions started per second, on the UTC grid (one takes 5.40 ms)
LATE_WARN = 0.0003                  # a START later than this past its tick counts as late
COMPS = 'ZNE'
PINS = {'Z': (24, 27), 'N': (25, 22), 'E': (5, 6)}   # BCM: (CS, DRDY) = board pins (18, 13) (22, 15) (29, 31)
GPIOCHIP = '/dev/gpiochip0'                         # Pi 5: the RP1 header bank

VREF_ADC = 2.048
SENS = 1500.0                           # V/(m/s), differential, both halves
DIV_GAIN = 4.99e3 / (4.99e3 + 10e3)     # ADC board level shifter
NM_S_PER_COUNT = VREF_ADC / 2 ** 23 / (SENS * DIV_GAIN) * 1e9     # 0.489 nm/s
FULL_SCALE_MM_S = VREF_ADC / (SENS * DIV_GAIN) * 1e3              # 4.1 mm/s

# ------------------------------------------------------------------ ADS1115 --
MASS_ADDR = 0x48
MASS_CHAN = (('Z', 0), ('N', 1), ('E', 2))     # A3 = VREF
MASS_WARN_V = 2.5                               # |mass| beyond this: suggest CENTRE
MASS_STOP_V = 7.0                               # beyond this: on the stops (locked or fallen)

FS_OUT = 100.0


class TriADS1220:
    """Three ADS1220s on SPI0, single-shot, converted together, read together.

    CS and DRDY through libgpiod (the Pi 5's RP1 bank). DRDY is only looked at
    as a level, once all three conversions should be done: any SPI read on the
    shared bus sends the other modules' DRDY high again (the data register
    is intact), so nothing is read until all three are low."""

    def __init__(self, speed=1_000_000):
        import gpiod
        import spidev
        from gpiod.line import Direction, Value, Bias
        self.V = Value
        self.cs = [PINS[c][0] for c in COMPS]
        self.drdy = [PINS[c][1] for c in COMPS]
        self.req = gpiod.request_lines(GPIOCHIP, consumer='cmgcapture', config={
            tuple(self.cs): gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE),
            tuple(self.drdy): gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)})
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        try:
            self.spi.no_cs = True
        except OSError:
            pass                        # Pi 5: CE0 on pin 24 toggles into thin air
        self.spi.mode = 1
        self.spi.max_speed_hz = speed
        self.conv_s = 0.0054            # measured in begin()
        self.t_done = 0.0

    def _xfer(self, cs, data):
        self.req.set_value(cs, self.V.INACTIVE)
        out = self.spi.xfer2(list(data))
        self.req.set_value(cs, self.V.ACTIVE)
        return out

    def begin(self):
        for cs in self.cs:
            self._xfer(cs, [RESET_CMD])
        time.sleep(0.002)
        for c, cs in zip(COMPS, self.cs):
            self._xfer(cs, [WREG | 3] + list(CONFIG))
            got = tuple(self._xfer(cs, [RREG | 3, 0, 0, 0, 0])[1:])
            if got != CONFIG:
                raise IOError("%s ADS1220 config readback %s, expected %s -- run ads_check.py"
                              % (c, ' '.join('%02X' % b for b in got),
                                 ' '.join('%02X' % b for b in CONFIG)))
        # time a few conversions: sets how long convert() sleeps before polling,
        # and the sample instant (the middle of the conversion)
        ts = []
        for _ in range(21):
            t = time.perf_counter()
            self.convert(sleep_first=False)
            ts.append(self.t_done - t)
        self.conv_s = sorted(ts)[len(ts) // 2]

    def _all_low(self):
        return all(v == self.V.INACTIVE for v in self.req.get_values(self.drdy))

    def convert(self, sleep_first=True):
        """START all three, wait for all three, read all three. Returns counts [Z, N, E]."""
        for cs in self.cs:
            self._xfer(cs, [START_CMD])
        t0 = time.perf_counter()
        if sleep_first:
            time.sleep(max(0.0, self.conv_s - 0.0006))
        while not self._all_low():
            if time.perf_counter() - t0 > 0.05:
                bad = [c for c, v in zip(COMPS, self.req.get_values(self.drdy)) if v != self.V.INACTIVE]
                raise IOError("no DRDY from %s 50 ms after START" % ''.join(bad))
        self.t_done = time.perf_counter()
        out = []
        for cs in self.cs:
            b = self._xfer(cs, [RDATA, 0, 0, 0])
            v = (b[1] << 16) | (b[2] << 8) | b[3]
            out.append(v - (1 << 24) if v & 0x800000 else v)
        return out

    def close(self):
        for cs in self.cs:
            try:
                self._xfer(cs, [POWERDOWN])
            except Exception:
                pass
        self.spi.close()
        self.req.release()


# ---------------------------------------------------------------- resampler --
class Resampler:
    """One channel onto the UTC grid k / fs_out, by a Kaiser-windowed sinc.

    Fed (seg, anchor, fs, i0, data): input sample i of segment seg is at
    anchor + i / fs. fs is refined as the run goes on, so the held samples are
    re-timed with the latest value each call -- the change is microseconds.
    A new segment means a gap: the held samples are dropped and the output
    starts a new segment of its own."""

    KEEP = 256                  # spare input samples held behind the kernel (~1.4 s)

    def __init__(self, fs_out=FS_OUT, fc=40.0, half=48, beta=8.0):
        self.fs_out, self.fc, self.half, self.beta = fs_out, fc, half, beta
        self.seg = None
        self.oseg = -1
        self.x = np.empty(0)
        self.xi0 = 0             # segment index of x[0]
        self.next_k = None       # grid index of the next output sample

    def feed(self, seg, anchor, fs, i0, data):
        """Returns (t0, oseg, samples) of new output, or None."""
        if seg != self.seg or i0 != self.xi0 + self.x.size:
            self.seg, self.oseg = seg, self.oseg + 1
            self.x, self.xi0 = np.asarray(data, np.float64), i0
            # the first output whose kernel has all its left taps
            self.next_k = math.ceil((anchor + (i0 + self.half) / fs) * self.fs_out)
        else:
            self.x = np.concatenate([self.x, data])
        h = self.half
        # last grid point whose kernel has all its right taps
        k_last = math.floor((anchor + (self.xi0 + self.x.size - 1 - h) / fs) * self.fs_out)
        if k_last < self.next_k:
            return None
        k = np.arange(self.next_k, k_last + 1)
        p = (k / self.fs_out - anchor) * fs - self.xi0         # fractional position in x
        base = np.floor(p).astype(np.int64)
        if base[0] - h + 1 < 0:
            # a rate refinement moved the held samples by more than the spare
            # history: start a new output segment next block rather than index
            # off the front (numpy would wrap a negative index silently)
            log("resampler: timing moved %.0f samples, more than the %d held -- new output segment"
                % (h - 1 - base[0], self.KEEP))
            self.seg = None
            return None
        j = base[:, None] + np.arange(-h + 1, h + 1)[None, :]  # 2h taps around p
        d = j - p[:, None]
        r = 2 * self.fc / fs
        w = r * np.sinc(r * d) * np.i0(self.beta * np.sqrt(np.clip(1 - (d / h) ** 2, 0, 1))) / np.i0(self.beta)
        w /= w.sum(axis=1, keepdims=True)                      # exact DC gain
        y = (w * self.x[j]).sum(axis=1)
        t0 = self.next_k / self.fs_out
        self.next_k = k_last + 1
        # keep what the next output's kernel can still reach, plus spare history
        # for a rate refinement to move the timeline by. The first refinement, 60 s
        # in, moved it ~10 samples (the oscillators speed up ~0.1 % as the chips
        # warm after START); with 4 spare that cost a 1.6 s gap.
        p_next = (self.next_k / self.fs_out - anchor) * fs - self.xi0
        drop = max(0, int(math.floor(p_next)) - h - self.KEEP)
        if drop:
            self.x = self.x[drop:]
            self.xi0 += drop
        return t0, self.oseg, y.astype(np.float32)


# ------------------------------------------------------------ mass positions --
class MassReader(threading.Thread):
    """ADS1115 once a second, on the second: Vmass = (Vin - 0.9009 VREF) / 0.0991."""

    def __init__(self, writers, stop):
        super().__init__(daemon=True)
        self.writers, self.stop = writers, stop
        self.latest = None
        self.seg = 0
        self.t_prev = None
        self.flush_every = 60

    def run(self):
        try:
            from smbus2 import SMBus
            import aux_check
        except ImportError as e:
            log("mass positions disabled: %s" % e)
            return
        n = 0
        warned = 0.0
        with SMBus(1) as bus:
            while not self.stop.is_set():
                t = math.floor(time.time()) + 1
                time.sleep(max(0.0, t - time.time()))
                try:
                    v = aux_check.read_all(bus)
                except OSError as e:
                    log("ADS1115 read failed: %s" % e)
                    time.sleep(5)
                    continue
                vref = v[3]
                mass = [(v[ch] - 0.9009 * vref) / 0.0991 for _, ch in MASS_CHAN]
                self.latest = mass
                if self.t_prev is not None and t != self.t_prev + 1:
                    self.seg += 1
                self.t_prev = t
                if self.writers:
                    for (c, _), m, w in zip(MASS_CHAN, mass, self.writers):
                        w.add(float(t), 1.0, self.seg, np.array([m], np.float32))
                    n += 1
                    if n % self.flush_every == 0:
                        for w in self.writers:
                            w.flush()
                far = [c for (c, _), m in zip(MASS_CHAN, mass) if MASS_WARN_V < abs(m) < MASS_STOP_V]
                stop = [c for (c, _), m in zip(MASS_CHAN, mass) if abs(m) >= MASS_STOP_V]
                if (far or stop) and time.time() - warned > 600:
                    warned = time.time()
                    log("mass positions %s V:%s%s" % (
                        ' '.join('%s %+.2f' % (c, m) for (c, _), m in zip(MASS_CHAN, mass)),
                        ' %s off centre (CENTRE?)' % ''.join(far) if far else '',
                        ' %s on the stops (locked?)' % ''.join(stop) if stop else ''))
        for w in self.writers or []:
            w.close()


# ------------------------------------------------------------- RP1 keep-alive --
RP1_L1 = '/sys/bus/pci/devices/0002:01:00.0/link/l1_aspm'


def start_rp1_keepalive():
    """Fallback for a Pi 5 whose PCIe link to RP1 (the GPIO/SPI chip) may enter L1.

    With ASPM L1 on that link (the default), an ~80 ms disturbance reaches all
    three ADC channels every 2.77 s whenever the bus idles between reads. The
    fix is pcie_aspm=off in /boot/firmware/cmdline.txt (see README). Without
    it, this keeps the link busy at the cost of one core. Skipped when L1 is
    off or ASPM is disabled."""
    import subprocess
    try:
        with open(RP1_L1) as f:
            if f.read().strip() == '0':
                log("RP1 PCIe L1 is off: no keep-alive needed")
                return None
    except OSError:
        return None
    code = ("import gpiod\n"
            "from gpiod.line import Direction\n"
            "r = gpiod.request_lines('%s', consumer='rp1-keepalive', config={(17,): "
            "gpiod.LineSettings(direction=Direction.INPUT)})\n"
            "while True:\n    r.get_value(17)\n" % GPIOCHIP)
    p = subprocess.Popen([sys.executable, '-c', code])
    log("RP1 PCIe L1 is on: keep-alive process %d started (one core busy); "
        "disable L1 as root to drop it" % p.pid)
    return p


# ---------------------------------------------------------------------- main --
def make_writer(opts, channel):
    o = dict(opts, channel=channel)
    if opts['logFormat'] == 'mseed' and HAVE_OBSPY:
        return MSeedWriter(o)
    return CsvWriter(o)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--no-log', action='store_true', help='stream only, write nothing to disk')
    args = ap.parse_args()

    opts = load_config()
    sys.setswitchinterval(0.0005)       # the tick loop must get the GIL back within a fraction of a period
    wait_for_ntp()

    keepalive = start_rp1_keepalive()
    adc = TriADS1220()
    adc.begin()
    delay = adc.conv_s / 2              # sample instant: the middle of the conversion
    log("ADS1220 x3 single-shot turbo, %.0f SPS synchronised (conversion %.2f ms); out %.0f SPS on the "
        "UTC grid; 1 count = %.3f nm/s, full scale %.1f mm/s"
        % (FS_IN, adc.conv_s * 1e3, FS_OUT, NM_S_PER_COUNT, FULL_SCALE_MM_S))

    chans = [opts['channelPrefix'] + c for c in COMPS]
    if args.no_log:
        writers, mwriters = None, None
    else:
        if opts['logFormat'] == 'mseed' and not HAVE_OBSPY:
            log("obspy not importable -- falling back to gzip CSV")
        writers = [make_writer(opts, ch) for ch in chans]
        mwriters = [make_writer(opts, opts['massPrefix'] + c) for c in COMPS]
        log("logging %s + %s to %s" % (' '.join(chans), ' '.join(opts['massPrefix'] + c for c in COMPS),
                                        opts['logDir']))
        prune_log(opts)

    hist = int(opts['historySec'] / opts['blockSec'])
    bcs = [Broadcaster(opts[c.lower() + 'Port'], history_blocks=hist) for c in COMPS]
    rs = [Resampler() for _ in COMPS]
    flush_blocks = max(1, int(round(opts['logFlushSec'] / opts['blockSec'])))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    mass = MassReader(mwriters, stop)
    mass.start()

    # resampling, disk and network on their own thread; the read loop only queues
    sink_q = queue.Queue(maxsize=600)
    peak = [0.0] * 3

    def sink():
        blocks, last_prune = [0] * 3, time.time()
        while True:
            item = sink_q.get()
            if item is None:
                break
            i, seg, anchor, fs, i0, data, reanchored = item
            out = rs[i].feed(seg, anchor, fs, i0, data)
            if out is None:
                continue
            t0, oseg, y = out
            peak[i] = max(peak[i], float(np.abs(y).max()))
            bcs[i].publish(pack_block(t0, FS_OUT, oseg, y))
            if writers:
                writers[i].add(t0, FS_OUT, oseg, y)
                blocks[i] += 1
                if reanchored or blocks[i] % flush_blocks == 0:
                    writers[i].flush()
                if time.time() - last_prune > 3600:
                    last_prune = time.time()
                    try:
                        prune_log(opts)
                    except OSError as e:
                        log("prune failed: %s" % e)
        for w in writers or []:
            w.close()

    sink_t = threading.Thread(target=sink, daemon=True)
    sink_t.start()

    period = 1.0 / FS_IN
    n_block = max(1, int(round(opts['blockSec'] * FS_IN)))
    seg = 0
    t_next = math.ceil(time.time() * FS_IN + 1) / FS_IN       # the tick after next
    anchor = t_next + delay             # UTC time of segment sample 0
    i_seg = 0                           # segment index of the next sample
    blk_i0, bufs = 0, [[], [], []]
    prev = None
    n_conv, missed, late, late_max = 0, 0, 0, 0.0
    t_status = time.time()
    log("capturing")
    try:
        while not stop.is_set():
            dt = t_next - time.time()
            stepped_back = dt > 1.0
            if 0.0015 < dt <= 1.0:
                time.sleep(dt - 0.001)
            while not stepped_back and time.time() < t_next:
                pass
            now = time.time()
            behind = now - t_next
            if behind > 0.5 or stepped_back:
                # a stall or a clock step: close the segment, start a fresh grid
                log("timing jump %+.3f s -- new segment %d" % (behind, seg + 1))
                if bufs[0]:
                    for i in range(3):
                        sink_q.put((i, seg, anchor, FS_IN, blk_i0,
                                    np.array(bufs[i], np.float64) * NM_S_PER_COUNT, True))
                seg += 1
                t_next = math.ceil(now * FS_IN + 1) / FS_IN
                anchor, i_seg, blk_i0, bufs, prev = t_next + delay, 0, 0, [[], [], []], None
                continue
            skipped = 0
            if behind >= period:
                # whole ticks missed (the process was held off): fill them below
                skipped = int(behind * FS_IN)
                t_next += skipped * period
                behind -= skipped * period
            v = adc.convert()
            n_conv += 1
            if behind > LATE_WARN:
                late += 1
            late_max = max(late_max, behind)
            if skipped:
                missed += skipped
                p = prev or v
                for i in range(3):
                    bufs[i].extend(p[i] + (v[i] - p[i]) * (k + 1) / (skipped + 1) for k in range(skipped))
                i_seg += skipped
            for i in range(3):
                bufs[i].append(v[i])
            prev = v
            i_seg += 1
            t_next += period
            if len(bufs[0]) >= n_block:
                for i in range(3):
                    try:
                        sink_q.put_nowait((i, seg, anchor, FS_IN, blk_i0,
                                           np.array(bufs[i], np.float64) * NM_S_PER_COUNT, False))
                    except queue.Full:
                        log("sink queue full -- block dropped")
                blk_i0, bufs = i_seg, [[], [], []]
            if now - t_status >= STATUS_EVERY:
                m = mass.latest
                log("%.2f SPS  seg %d  missed %d  late %d (max %.2f ms)  clients %d  queued %d  "
                    "peak %s nm/s  mass %s V"
                    % (n_conv / (now - t_status), seg, missed, late, late_max * 1e3,
                       sum(b.count for b in bcs), sink_q.qsize(),
                       '/'.join('%.0f' % p for p in peak),
                       ' '.join('%+.2f' % x for x in m) if m else '--'))
                peak[:] = [0.0] * 3
                t_status, n_conv, late, late_max = now, 0, 0, 0.0
    finally:
        adc.close()
        sink_q.put(None)
        sink_t.join(timeout=10)
        stop.set()
        mass.join(timeout=3)
        if keepalive:
            keepalive.terminate()
        log("stopped")


if __name__ == '__main__':
    main()
