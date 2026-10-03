#!/usr/bin/env python3
"""Live display for the CMG-3T: Z, N and E traces, with spectrograms under the chosen ones.

    python cmgclient.py                 # host and ports from cmg3t.JSON
    python cmgclient.py 10.0.0.102      # or name the host
    python cmgclient.py --fresh         # ignore the Pi's replay, start with an empty window
    python cmgclient.py --spec ZNE      # spectrograms for all three (default: Z only)
    python cmgclient.py --mp            # start in the 1-50 s view
    python cmgclient.py --lp            # start in the 10-100 s view

Keys in the plot window: l cycles the views short period -> 1-50 s -> 10-100 s,
c clears.

Long-period view: the last hour, each trace decimated to 2 SPS and band-passed
0.01-0.1 Hz (10-100 s: surface waves of distant quakes, the ~6 s microseism
just above the band), spectrogram 0-0.5 Hz with a 64 s window. The 3T is flat
to 120 s, so no response correction is needed in this band. Settings under
"longPeriod" in cmg3t.JSON. The 1-50 s view ("midPeriod": 5 SPS, 0.02-1 Hz,
spectrogram 0-1 Hz) is the microseism's own band and regional quakes; the
10-100 s one sits below the microseism, where distant quakes' surface waves
stand out. The Pi replays historySec (1 h) on connect, so
the hour fills at once.

The Pi (../pi/cmgcapture.py) streams each component on its own port
in the GEO1 format, 100 SPS on an exact UTC grid, so the three line up sample
for sample. The stream is raw velocity: no DC block, no high-pass (the 3T is
flat to 120 s). Each trace is band-passed cutLow-cutHigh for display, and its
own mean is the 0 line.
"""
import os
import sys
import threading
import time
import bisect
from collections import deque
from datetime import datetime

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.animation import FuncAnimation
from scipy import signal
from scipy.ndimage import uniform_filter1d

from loadOpts import load_config, ROOT
from geostream import connect, read_block

opts = load_config(os.path.join(ROOT, 'cmg3t.JSON'))
FRESH = '--fresh' in sys.argv
_args = [a for i, a in enumerate(sys.argv[1:], 1)
         if not a.startswith('--') and sys.argv[i - 1] != '--spec']
HOST = _args[0] if _args else opts['serverIP']
COMPS = 'ZNE'
PORTS = [opts[c.lower() + 'Port'] for c in COMPS]
# the views: short period = the top-level settings, then "midPeriod" and "longPeriod"
VIEW_KEYS = ('plotSeconds', 'cutLow', 'cutHigh', 'filterOrder', 'decimateTo', 'specWindow',
             'specHopSec', 'specSmoothSec', 'specMinHz', 'specMaxHz', 'specHighpassHz', 'specPad')
opts.setdefault('decimateTo', 0)
VIEWS = {'SP': {k: opts[k] for k in VIEW_KEYS}}
VIEWS['LP'] = dict(VIEWS['SP'], plotSeconds=3600, cutLow=0.01, cutHigh=0.1, decimateTo=2.0,
                   specWindow=128, specHopSec=8, specSmoothSec=0, specMinHz=0, specMaxHz=0.5,
                   specHighpassHz=0, specPad=4)
VIEWS['LP'].update(opts.get('longPeriod', {}))
VIEWS['MP'] = dict(VIEWS['LP'], cutLow=0.02, cutHigh=1.0, decimateTo=5.0, specWindow=320, specMaxHz=1.0)
VIEWS['MP'].update(opts.get('midPeriod', {}))
CYCLE = ['SP', 'MP', 'LP']
VIEW_NAME = {'SP': 'short period', 'MP': '1–50 s', 'LP': '10–100 s'}
HOLD_SEC = max(v['plotSeconds'] for v in VIEWS.values()) + 30
# which components get a spectrogram: "specComponents" in cmg3t.JSON (default Z),
# or --spec ZNE / --spec Z / --spec none on the command line
_spec = [a.split('=', 1)[1] for a in sys.argv[1:] if a.startswith('--spec=')]
if '--spec' in sys.argv[1:-1]:
    _spec.append(sys.argv[sys.argv.index('--spec') + 1])
SPEC = (_spec[-1] if _spec else opts.get('specComponents', 'Z')).upper().replace('NONE', '')
COLORS = {'Z': '#DC9257', 'N': '#6FB7D9', 'E': '#8CCB7E'}


def _log(msg):
    print(datetime.now().strftime('%H:%M:%S ') + msg, flush=True)


class Receiver(threading.Thread):
    """One component: keeps the last plotSeconds (+ a margin) of blocks. Reconnects forever."""

    def __init__(self, comp, port):
        super().__init__(daemon=True)
        self.comp, self.port = comp, port
        self.blocks = deque()
        self.lock = threading.Lock()
        self.status = "connecting"
        self.t_min = time.time() - 5 if FRESH else 0.0

    def clear(self):
        with self.lock:
            self.blocks.clear()
            self.t_min = time.time() - 5

    def run(self):
        log = lambda m: _log("%s: %s" % (self.comp, m))
        while True:
            s = connect(HOST, self.port, log=log)
            self.status = "connected"
            try:
                while True:
                    t0, fs, seg, data = read_block(s)
                    with self.lock:
                        if t0 < self.t_min:
                            continue
                        # the Pi replays its history on connect: merge in time order, skip duplicates
                        if self.blocks and t0 <= self.blocks[-1][0] + 1e-3:
                            ts = [b[0] for b in self.blocks]
                            k = bisect.bisect_left(ts, t0 - 1e-3)
                            if k < len(ts) and abs(ts[k] - t0) <= 1e-3:
                                continue
                            self.blocks.insert(k, (t0, fs, seg, data))
                        else:
                            self.blocks.append((t0, fs, seg, data))
                        while (self.blocks[-1][0] - self.blocks[0][0]) > HOLD_SEC:
                            self.blocks.popleft()
            except (OSError, ConnectionError) as e:
                log("stream lost: %s" % e)
                self.status = "reconnecting"
                try:
                    s.close()
                except OSError:
                    pass
                time.sleep(2)

    def snapshot(self):
        with self.lock:
            if not self.blocks:
                return None
            blocks = list(self.blocks)
        fs = blocks[-1][1]
        t = np.concatenate([b[0] + np.arange(b[3].size) / b[1] for b in blocks])
        x = np.concatenate([b[3] for b in blocks]).astype(np.float64)
        return t, x, fs


# ------------------------------------------------------------------- display --
def bandpass(x, fs):
    lo, hi = opts['cutLow'], min(opts['cutHigh'], 0.45 * fs)
    x = x - x.mean()
    if lo <= 0:
        sos = signal.butter(opts['filterOrder'], hi, btype='low', fs=fs, output='sos')
    else:
        sos = signal.butter(opts['filterOrder'], [lo, hi], btype='band', fs=fs, output='sos')
    return signal.sosfiltfilt(sos, x)


def spectrogram_db(x, fs):
    nper = opts['specWindow']
    hop = min(nper - 1, max(1, int(round(opts['specHopSec'] * fs))))
    hp = opts['specHighpassHz']
    x = x - x.mean()
    if hp > 0:
        x = signal.sosfiltfilt(signal.butter(2, hp, btype='high', fs=fs, output='sos'), x)
    f, ts, S = signal.spectrogram(x, fs=fs, window='hann', nperseg=nper, nfft=nper * max(1, opts['specPad']),
                                  noverlap=nper - hop, detrend='constant', scaling='spectrum')
    db = 10 * np.log10(S + 1e-12)
    k = int(round(opts['specSmoothSec'] * fs / hop))
    if k > 1 and db.shape[1] > k:
        db = uniform_filter1d(db, k, axis=1, mode='nearest')
    if opts['specScale'] == 'root':
        db = 10 ** (db / 100)
    return f, ts, db


def envelope(t, y, n):
    """Min and max of each of n buckets: a long trace draws as ~2n points, spikes intact."""
    m = y.size // n
    if m < 2:
        return t, y
    k = n * m
    yy = y[:k].reshape(n, m)
    base = np.arange(n) * m
    idx = np.sort(np.concatenate([base + yy.argmin(axis=1), base + yy.argmax(axis=1),
                                  np.arange(k, y.size)]))
    return t[idx], y[idx]


def runs_of(t, x, fs):
    if t.size < 2:
        return [(t, x)]
    cut = np.flatnonzero(np.diff(t) > 1.5 / fs) + 1
    return list(zip(np.split(t, cut), np.split(x, cut)))


def sta_lta(y, fs):
    ns, nl = int(opts['STA'] * fs), int(opts['LTA'] * fs)
    if y.size < nl:
        return None
    env = np.abs(y)
    lta = env[-nl:].mean()
    return env[-ns:].mean() / lta if lta > 0 else None


plt.style.use('dark_background')
matplotlib.rcParams['toolbar'] = 'None'
# free this program's keys from matplotlib's defaults (l = log y-axis, c = back)
for _k, _key in (('keymap.yscale', 'l'), ('keymap.back', 'c')):
    matplotlib.rcParams[_k] = [x for x in matplotlib.rcParams[_k] if x != _key]
# each component's trace, with its spectrogram directly beneath it for those in SPEC
rows = []
for c in COMPS:
    rows.append(('tr', c))
    if c in SPEC:
        rows.append(('sp', c))
fig, axes = plt.subplots(len(rows), 1, figsize=(opts['figW'], opts['figH']), sharex=True,
                         gridspec_kw={'height_ratios': [1 if kind == 'tr' else 1.4 for kind, _ in rows]})
axes = list(np.atleast_1d(axes))
ax_tr = [ax for ax, (kind, _) in zip(axes, rows) if kind == 'tr']
ax_sp = {c: ax for ax, (kind, c) in zip(axes, rows) if kind == 'sp'}
fig.canvas.manager.set_window_title("cmgclient  %s:%s" % (HOST, '/'.join(map(str, PORTS))))
rx = [Receiver(c, p) for c, p in zip(COMPS, PORTS)]
for r in rx:
    r.start()
state = {'laid_out': False, 'last_trigger': 0.0, 'view': 'LP' if '--lp' in sys.argv else 'MP' if '--mp' in sys.argv else 'SP'}
opts.update(VIEWS[state['view']])


def on_key(ev):
    if ev.key == 'l':
        state['view'] = CYCLE[(CYCLE.index(state['view']) + 1) % len(CYCLE)]
        opts.update(VIEWS[state['view']])
        _log("%s view" % VIEW_NAME[state['view']])
    elif ev.key == 'c':
        for r in rx:
            r.clear()
        _log("display cleared")


fig.canvas.mpl_connect('key_press_event', on_key)


def animate(_):
    t_frame = time.time()
    _animate()
    took = time.time() - t_frame
    if took > opts['refreshMs'] / 1000.0:
        print("slow frame: %.2f s against a %d ms refresh" % (took, opts['refreshMs']), flush=True)


def _animate():
    snaps = [r.snapshot() for r in rx]
    if not any(snaps):
        ax_tr[0].set_title("%s  |  %s" % (rx[0].status, HOST), fontsize=opts['titleFontSize'])
        return
    t_right = max([s[0][-1] for s in snaps if s] + [time.time()])
    t_left = t_right - opts['plotSeconds']
    td = mdates.date2num([datetime.fromtimestamp(v) for v in (t_left, t_right)])
    to_num = lambda tt: td[0] + (tt - t_left) / 86400.0
    n_px = int(fig.get_size_inches()[0] * fig.dpi)
    ratio = None
    lag = None
    fs_seen = None

    for ax, comp, snap in zip(ax_tr, COMPS, snaps):
        sp = ax_sp.get(comp)
        ax.clear()
        if sp:
            sp.clear()
        ax.set_ylabel("%s  µm/s" % comp, color=COLORS[comp])
        ax.margins(0, 0)
        if snap is None:
            continue
        t, x, fs = snap
        fs_seen = fs
        keep = t >= t_left
        t, x = t[keep], x[keep]
        q = int(round(fs / opts['decimateTo'])) if opts['decimateTo'] else 1
        fs_v = fs / q                              # rate the view works at
        min_run = max(int(10 * fs_v), 3 * opts['specWindow']) * q
        peak = 0.0
        trim = int(max(2.0, 0.5 / opts['cutLow']) * fs_v) if opts['cutLow'] > 0 else int(2 * fs_v)
        for tt, xx in runs_of(t, x, fs):
            if tt.size < min_run:
                continue
            if q > 1:
                # anti-aliased decimation for the long-period view: an hour at 100 SPS
                # is 360k points a channel, at 2 SPS it is 7200
                xx = signal.resample_poly(xx - xx.mean(), 1, q)
                tt = tt[::q][:xx.size]
            y = bandpass(xx, fs_v)
            core = y[trim:-trim] if y.size > 3 * trim else y
            peak = max(peak, float(np.abs(core).max()))
            te, ye = envelope(tt, y * 1e-3, 2 * n_px)
            ax.plot(to_num(te), ye, lw=opts['plotLineW'], color=COLORS[comp])
            if comp == 'Z':
                ratio = sta_lta(y, fs_v) if state['view'] == 'SP' else None
                lag = time.time() - tt[-1]
            if sp is None:
                continue
            f, ts, db = spectrogram_db(xx, fs_v)
            lo, hi = opts['specMinHz'], min(opts['specMaxHz'], fs_v / 2)
            sel = (f >= lo) & (f <= hi)
            vmin, vmax = np.percentile(db[sel], opts['specRangePct'])
            dt = ts[1] - ts[0] if ts.size > 1 else 1.0
            df = f[1] - f[0]
            sp.imshow(db, cmap='inferno', vmin=vmin, vmax=vmax, aspect='auto', origin='lower',
                      interpolation='antialiased',
                      extent=(to_num(tt[0] + ts[0] - dt / 2), to_num(tt[0] + ts[-1] + dt / 2),
                              f[0] - df / 2, f[-1] + df / 2))
        if peak:
            ax.set_ylim(-1.1e-3 * peak, 1.1e-3 * peak)

    title = "%s.%s  %s  %s  %g–%g Hz   %s SPS   lag %s" % (
        opts['network'], opts['station'], '/'.join(opts['channelPrefix'] + c for c in COMPS),
        VIEW_NAME[state['view']],
        opts['cutLow'], opts['cutHigh'],
        ('%.0f' % fs_seen + (' → %g' % opts['decimateTo'] if opts['decimateTo'] else '')) if fs_seen else '--',
        '%.1f s' % lag if lag is not None else '--')
    status = {r.status for r in rx}
    if status != {'connected'}:
        title += "   " + '/'.join("%s %s" % (r.comp, r.status) for r in rx if r.status != 'connected')
    color = 'white'
    if ratio is not None:
        title += "   Z STA/LTA %.2f" % ratio
        if ratio >= opts['staltaTrigger']:
            color = '#FF5555'
            if time.time() - state['last_trigger'] > opts['LTA']:
                state['last_trigger'] = time.time()
                print("%s  TRIGGER  Z STA/LTA %.2f" % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), ratio),
                      flush=True)
    ax_tr[0].set_title(title, fontsize=opts['titleFontSize'], color=color)

    for comp, sp in ax_sp.items():
        fs_view = opts['decimateTo'] or fs_seen or 100
        sp.set_ylim(opts['specMinHz'], min(opts['specMaxHz'], fs_view / 2))
        sp.set_ylabel("%s  Hz" % comp, color=COLORS[comp])
    bottom = axes[-1]
    bottom.set_xlim(td[0], td[1])
    loc = mdates.AutoDateLocator(minticks=6, maxticks=14)
    bottom.xaxis.set_major_locator(loc)
    bottom.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    bottom.tick_params(axis='x', labelsize=8)
    if not state['laid_out']:
        fig.tight_layout(pad=0.3, h_pad=0.2)
        state['laid_out'] = True


ani = FuncAnimation(fig, animate, interval=opts['refreshMs'], cache_frame_data=False)
plt.show()
