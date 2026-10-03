#!/usr/bin/env python3
"""Pi-side capture daemon: one process owns the ADS1220, logs to disk, streams to clients.

    python3 geocapture.py            # real hardware
    python3 geocapture.py --sim      # synthetic data, for testing the client without a Pi

On the geophone station it needs ads1220_capture.py (DIY-SEISMO) next to it -- that is the driver, the
DC block, the mains notch and the decimator; this file adds timing, logging and the
network. Only one process can own the SPI bus, which is why logging and streaming
live together in one process.

Timing. The ADS1220 is the clock: DRDY paces the reads and the chip's oscillator is
only good to a couple of percent, so sample times are NOT taken from time.time() per
sample. The capture anchors a segment at a wall-clock instant, numbers samples from
there, and refines the sample rate against the NTP-disciplined system clock as the
run goes on. If the two ever disagree by more than a sample (a stall, a suspend) it
starts a new segment; every block on the wire and every trace on disk carries its own
t0, fs and segment number, so a reader knows exactly which samples are contiguous.

Disk. Hourly files under logDir/YYYY/YYYYMMDD/, miniSEED if obspy is importable
(NET.STA..CHA.YYYYMMDD_HH.mseed, FLOAT32 in nm/s) or gzip CSV otherwise. Either way
a day's files merge into one obspy Stream.
"""
import argparse
import gzip
import math
import os
import queue
import signal
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from loadOpts import load_config
from geostream import pack_block, DEFAULT_PORT

try:
    from obspy import Trace, UTCDateTime
    HAVE_OBSPY = True
except ImportError:
    HAVE_OBSPY = False

RATE_REFINE_AFTER = 60.0      # seconds of segment before n/elapsed replaces the 3 s measurement
STALL_CHECKS = 3              # consecutive blocks over a sample of drift before re-anchoring
STEP_SEC = 0.5                # drift this large in one block is a clock step or a stall: re-anchor at once
NTP_WAIT = 60.0               # seconds to wait at startup for the system clock to be NTP-synced
STATUS_EVERY = 10.0           # seconds between status lines


def log(msg):
    print(datetime.now(timezone.utc).strftime('%H:%M:%S ') + msg, flush=True)


# ------------------------------------------------------------------ sources --
class ADCSource:
    """Real hardware: the ADS1220 driver plus its filter chain, yielding nm/s."""

    def __init__(self, opts):
        import ads1220_capture as A
        self.A = A
        self.adc = A.ADS1220()
        self.adc.begin()
        self.fs_raw = self.adc.measure_rate()
        self.adc.idle_sleep = opts['idleSleepFrac'] / self.fs_raw
        self.hp = A.dc_block(fs=self.fs_raw)
        self.nf = A.mains_notch(60.0, fs=self.fs_raw)
        self.dec = A.Decimator(A.DECIMATE, fs=self.fs_raw) if A.DECIMATE > 1 else None
        self.fs = self.fs_raw / (A.DECIMATE if self.dec else 1)
        log("ADS1220 %.2f SPS raw (%+.2f%% of nominal), %.2f SPS out, gain %d, full scale %.2f mm/s"
            % (self.fs_raw, 100 * (self.fs_raw / A.FS - 1), self.fs, A.GAIN,
               A.FULL_SCALE_MPS * 1e3))

    def read(self):
        """Blocks until the next OUTPUT sample; returns nm/s."""
        while True:
            v = self.nf(self.hp(self.adc.read_velocity()))
            if self.dec is None:
                return v * 1e9
            v = self.dec(v)
            if v is not None:
                return v * 1e9

    def close(self):
        self.adc.close()


class SimSource:
    """Synthetic ground: pink-ish noise, 60 Hz leak, a footstep every so often."""

    def __init__(self, fs=161.8):
        self.fs = fs
        self.fs_raw = fs * 2
        self.n = 0
        self.t_next = time.perf_counter()
        self.rng = np.random.default_rng()
        self.state = 0.0
        log("SIMULATED source at %.2f SPS" % fs)

    def read(self):
        self.t_next += 1.0 / self.fs
        dt = self.t_next - time.perf_counter()
        if dt > 0:
            time.sleep(dt)
        self.n += 1
        t = self.n / self.fs
        self.state = 0.98 * self.state + self.rng.normal(0, 60)      # low-frequency wander
        v = self.state + self.rng.normal(0, 150) + 40 * math.sin(2 * math.pi * 60 * t)
        if (self.n % int(self.fs * 20)) < int(self.fs * 1.5):        # a "footstep" every 20 s
            k = (self.n % int(self.fs * 20)) / self.fs
            v += 30000 * math.exp(-3 * k) * math.sin(2 * math.pi * 8 * k)
        return v

    def close(self):
        pass


# --------------------------------------------------------------------- clock --
def wait_for_ntp(max_wait=NTP_WAIT):
    """Block until timedatectl says the clock is NTP-synced, or max_wait runs out.

    A Pi without a battery RTC boots with the clock where it was at shutdown and
    NTP steps it forward minutes later. A segment anchored before that step gets
    every timestamp wrong by the downtime, so a capture started at boot waits here."""
    import subprocess
    t_end = time.time() + max_wait
    warned = False
    while True:
        try:
            r = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                               capture_output=True, text=True, timeout=5)
            synced = r.stdout.strip() == 'yes'
        except (OSError, subprocess.SubprocessError):
            return                                    # no systemd: nothing to wait for
        if synced:
            if warned:
                log("clock synced")
            return
        if time.time() >= t_end:
            log("WARNING: clock not NTP-synced after %.0f s -- timestamps may be off; "
                "a later step starts a new segment" % max_wait)
            return
        if not warned:
            log("waiting for NTP sync before anchoring timestamps...")
            warned = True
        time.sleep(2)


class SegmentClock:
    """Sample times from an anchor and a refined rate, not from per-sample wall time."""

    def __init__(self, fs):
        self.fs = fs
        self.seg = 0
        self.over = 0
        self.reanchor(time.time())

    def reanchor(self, t_first):
        """t_first is the wall time of sample 0 of the new segment."""
        self.t_anchor = t_first
        self.n = 0
        self.over = 0
        self.refined = False      # fs not yet fitted to this segment's wall time

    def t_of(self, index):
        return self.t_anchor + index / self.fs

    def advance(self, count):
        self.n += count

    def check(self):
        """Call after a block. Refines fs; re-anchors on a stall. Returns True if re-anchored."""
        # 'now' is just after sample n-1 was read; sample 0 was read at the anchor.
        # Drift is measured against the fs we have BEFORE refining it: refining
        # first would fit fs to 'now' and make the drift zero by construction, so a
        # stall -- or NTP stepping the clock under us -- would vanish into a wrong
        # sample rate instead of starting a new segment. (That happened: a capture
        # started at boot absorbed a 4.5 min clock step and streamed 148 SPS.)
        now = time.time()
        elapsed = now - self.t_anchor
        drift = now - self.t_of(self.n - 1)
        if abs(drift) > STEP_SEC:
            log("clock jumped %+.2f s (step or stall) -- new segment %d" % (drift, self.seg + 1))
            self.seg += 1
            self.reanchor(now + 1.0 / self.fs)
            return True
        # Until the segment is RATE_REFINE_AFTER long, fs is the driver's 3 s
        # measurement (or the last segment's fit) and its error -- 0.03 % is a
        # sample every 20 s -- accumulates as drift; only the step check above runs.
        # The first fit absorbs that drift unconditionally. From then on fs is
        # fitted through every on-time block, so the sample-level check below sees
        # one block of rate error plus jitter and nothing else. The wait is what
        # makes the fit robust: a 4 ms hiccup on a 1 s baseline would bend fs by
        # 0.4 %, on 60 s it is noise.
        if not self.refined:
            if elapsed > RATE_REFINE_AFTER:
                self.fs = (self.n - 1) / elapsed
                self.refined = True
            return False
        # Scheduling jitter shows up as a one-off excursion; a real stall (samples
        # lost while the process was held off the ADC) stays. Only the latter
        # earns a new segment, anchored at the next sample, not at this noisy 'now'.
        self.over = self.over + 1 if abs(drift) > 1.0 / self.fs else 0
        if self.over >= STALL_CHECKS:
            log("clock drift %.1f ms for %d blocks -- new segment %d"
                % (drift * 1e3, STALL_CHECKS, self.seg + 1))
            self.seg += 1
            self.reanchor(now + 1.0 / self.fs)
            return True
        if self.over == 0:
            self.fs = (self.n - 1) / elapsed
        return False


# ------------------------------------------------------------------- writers --
class MSeedWriter:
    def __init__(self, opts):
        self.opts = opts
        self.root = opts['logDir']
        self.buf, self.t0, self.seg, self.fs = [], None, None, None

    def _path(self, t0):
        d = datetime.fromtimestamp(t0, timezone.utc)
        folder = os.path.join(self.root, d.strftime('%Y'), d.strftime('%Y%m%d'))
        os.makedirs(folder, exist_ok=True)
        o = self.opts
        return os.path.join(folder, "%s.%s..%s.%s.mseed"
                            % (o['network'], o['station'], o['channel'], d.strftime('%Y%m%d_%H')))

    def add(self, t0, fs, seg, data):
        if self.buf and seg != self.seg:
            self.flush()
        if not self.buf:
            self.t0, self.seg, self.fs = t0, seg, fs
        self.buf.append(data)

    def flush(self):
        if not self.buf:
            return
        data = np.concatenate(self.buf).astype(np.float32)
        tr = Trace(data=data)
        o = self.opts
        tr.stats.network, tr.stats.station = o['network'], o['station']
        tr.stats.channel, tr.stats.location = o['channel'], ''
        tr.stats.starttime = UTCDateTime(self.t0)
        tr.stats.sampling_rate = self.fs
        with open(self._path(self.t0), 'ab') as fh:
            tr.write(fh, format='MSEED', encoding='FLOAT32', reclen=512)
        self.buf = []

    def close(self):
        self.flush()


class CsvWriter:
    """Fallback when obspy is missing: gzip CSV, one line per sample, UTC unix time and nm/s."""

    def __init__(self, opts):
        self.opts = opts
        self.root = opts['logDir']
        self.fh, self.hour = None, None

    def _open(self, t0):
        d = datetime.fromtimestamp(t0, timezone.utc)
        folder = os.path.join(self.root, d.strftime('%Y'), d.strftime('%Y%m%d'))
        os.makedirs(folder, exist_ok=True)
        o = self.opts
        path = os.path.join(folder, "%s.%s..%s.%s.csv.gz"
                            % (o['network'], o['station'], o['channel'], d.strftime('%Y%m%d_%H')))
        new = not os.path.exists(path)
        self.fh = gzip.open(path, 'at')
        if new:
            self.fh.write("# utc_unix_s,velocity_nm_per_s\n")
        self.hour = d.strftime('%Y%m%d_%H')

    def add(self, t0, fs, seg, data):
        hour = datetime.fromtimestamp(t0, timezone.utc).strftime('%Y%m%d_%H')
        if self.fh is None or hour != self.hour:
            self.close()
            self._open(t0)
        t = t0 + np.arange(data.size) / fs
        self.fh.write("\n".join("%.4f,%.1f" % (a, b) for a, b in zip(t, data)) + "\n")

    def flush(self):
        if self.fh:
            self.fh.flush()

    def close(self):
        if self.fh:
            self.fh.close()
            self.fh = None


# ------------------------------------------------------------------- pruning --
def prune_log(opts):
    """Delete the oldest UTC-day folders until both limits hold: nothing older than
    retainDays, and the whole log under maxLogGB. Either limit <= 0 is 'no limit'.
    Runs at startup and once an hour from the sink thread; today is never deleted."""
    root = opts['logDir']
    days = []
    for year in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        ydir = os.path.join(root, year)
        if not (year.isdigit() and os.path.isdir(ydir)):
            continue
        for day in sorted(os.listdir(ydir)):
            ddir = os.path.join(ydir, day)
            if day.isdigit() and len(day) == 8 and os.path.isdir(ddir):
                size = sum(os.path.getsize(os.path.join(ddir, f)) for f in os.listdir(ddir))
                days.append((day, ddir, size))
    if not days:
        return
    today = datetime.now(timezone.utc).strftime('%Y%m%d')
    total = sum(sz for _, _, sz in days)
    cap = opts['maxLogGB'] * 1e9 if opts['maxLogGB'] > 0 else float('inf')
    keep_days = opts['retainDays']
    for day, ddir, size in days:                       # oldest first
        if day == today:
            break
        age = (datetime.strptime(today, '%Y%m%d') - datetime.strptime(day, '%Y%m%d')).days
        if (keep_days > 0 and age > keep_days) or total > cap:
            for f in os.listdir(ddir):
                os.remove(os.path.join(ddir, f))
            os.rmdir(ddir)
            ydir = os.path.dirname(ddir)
            if not os.listdir(ydir):
                os.rmdir(ydir)
            total -= size
            log("pruned %s (%.0f MB, %d days old); log now %.2f GB" % (day, size / 1e6, age, total / 1e9))
        else:
            break                                       # everything after this is newer


# --------------------------------------------------------------- broadcaster --
class Broadcaster:
    """Accepts any number of clients; each gets every block. Slow clients are dropped."""

    def __init__(self, port, history_blocks=0):
        self.clients = []
        self.lock = threading.Lock()
        # the last few minutes of frames, replayed to every new connection so a
        # client that restarts or rides out a network outage gets its window back
        self.history = deque(maxlen=history_blocks) if history_blocks else None
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(('0.0.0.0', port))
        self.srv.listen(8)
        threading.Thread(target=self._accept, daemon=True).start()
        log("serving on port %d" % port)

    def _accept(self):
        while True:
            try:
                conn, addr = self.srv.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self.lock:
                backlog = list(self.history) if self.history else []
                q = queue.Queue(maxsize=len(backlog) + 120)
                for frame in backlog:
                    q.put_nowait(frame)
                self.clients.append((conn, addr, q))
            threading.Thread(target=self._send, args=(conn, addr, q), daemon=True).start()
            log("client %s:%d connected (%d total)" % (addr[0], addr[1], len(self.clients)))

    def _send(self, conn, addr, q):
        try:
            while True:
                conn.sendall(q.get())
        except OSError:
            pass
        finally:
            self._drop(conn, addr)

    def _drop(self, conn, addr):
        with self.lock:
            self.clients = [c for c in self.clients if c[0] is not conn]
        try:
            conn.close()
        except OSError:
            pass
        log("client %s:%d gone (%d left)" % (addr[0], addr[1], len(self.clients)))

    def publish(self, frame):
        with self.lock:
            if self.history is not None:
                self.history.append(frame)
            targets = list(self.clients)
        for conn, addr, q in targets:
            try:
                q.put_nowait(frame)
            except queue.Full:
                log("client %s:%d too slow, dropping" % addr)
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    @property
    def count(self):
        return len(self.clients)


# ---------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--sim', action='store_true', help='synthetic data instead of the ADS1220')
    ap.add_argument('--no-log', action='store_true', help='stream only, write nothing to disk')
    args = ap.parse_args()

    opts = load_config()
    if not args.sim:
        wait_for_ntp()
    src = SimSource() if args.sim else ADCSource(opts)

    if args.no_log:
        writer = None
    elif opts['logFormat'] == 'mseed' and HAVE_OBSPY:
        writer = MSeedWriter(opts)
        log("logging miniSEED to %s" % opts['logDir'])
    else:
        if opts['logFormat'] == 'mseed':
            log("obspy not importable -- falling back to gzip CSV")
        writer = CsvWriter(opts)
        log("logging CSV to %s" % opts['logDir'])

    if writer:
        prune_log(opts)
    bc = Broadcaster(opts.get('serverPort', DEFAULT_PORT),
                     history_blocks=int(opts['historySec'] / opts['blockSec']))
    clock = SegmentClock(src.fs)
    n_block = max(1, int(round(opts['blockSec'] * src.fs)))
    flush_blocks = max(1, int(round(opts['logFlushSec'] / opts['blockSec'])))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    # Disk and network live on their own thread. The ADS1220 overwrites its data
    # register every conversion (~3 ms), so the read loop must never wait on an
    # SD-card write or a slow socket -- it only ever hands a block to this queue.
    sink_q = queue.Queue(maxsize=600)

    def sink():
        blocks, last_prune = 0, time.time()
        while True:
            item = sink_q.get()
            if item is None:
                break
            t0, fs, seg, data, flush_now = item
            bc.publish(pack_block(t0, fs, seg, data))
            if writer:
                writer.add(t0, fs, seg, data)
                blocks += 1
                if flush_now or blocks % flush_blocks == 0:
                    writer.flush()
                if time.time() - last_prune > 3600:
                    last_prune = time.time()
                    try:
                        prune_log(opts)
                    except OSError as e:
                        log("prune failed: %s" % e)
        if writer:
            writer.close()

    sink_t = threading.Thread(target=sink, daemon=True)
    sink_t.start()

    buf = np.empty(n_block, dtype=np.float32)
    peak, t_status = 0.0, time.time()
    # the first sample defines the anchor; the block loop then counts from it
    buf[0] = src.read()
    clock.reanchor(time.time())
    i = 1
    log("capturing: %d-sample blocks, flush every %d blocks" % (n_block, flush_blocks))
    try:
        while not stop.is_set():
            while i < n_block:
                buf[i] = src.read()
                i += 1
            t0 = clock.t_of(clock.n)
            fs, seg = clock.fs, clock.seg
            data = buf.copy()
            clock.advance(n_block)
            i = 0
            reanchored = clock.check()
            try:
                sink_q.put_nowait((t0, fs, seg, data, reanchored))
            except queue.Full:
                log("sink queue full -- disk or network is not keeping up, block dropped")

            peak = max(peak, float(np.abs(data).max()))
            now = time.time()
            if now - t_status >= STATUS_EVERY:
                log("%.3f SPS  seg %d  clients %d  queued %d  peak %9.1f nm/s"
                    % (clock.fs, clock.seg, bc.count, sink_q.qsize(), peak))
                peak, t_status = 0.0, now
    finally:
        src.close()
        sink_q.put(None)
        sink_t.join(timeout=10)
        log("stopped")


if __name__ == '__main__':
    main()
