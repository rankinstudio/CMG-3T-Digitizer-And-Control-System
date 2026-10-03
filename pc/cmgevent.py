#!/usr/bin/env python3
"""CMG-3T plots of one catalogued earthquake, in the three cmgclient.py views.

    python cmgevent.py us6000tz62            # a USGS event id
    python cmgevent.py us6000tz62 --force    # save the plots even if it didn't register

Looks the event up in the USGS catalogue, predicts the P / S arrivals at the
station (iasp91) and the surface waves (3.5-2.5 km/s), pulls the HH? hours
that cover them from the CMG-3T Pi, and band-passes Z, N, E in the three bands
of cmg3t.JSON: short period (cutLow-cutHigh), "midPeriod" and "longPeriod".
The event registers when, in any band, Z's loudest stretch in the window where
that band's waves should be (SP: the 2 min after P and after S, so local
bangs in between don't count; MP: P to the 2.5 km/s surface
waves + 5 min; LP: the surface waves, 3.5 to 2.5 km/s, +-) is SNR_MIN times
the loudest stretch of the same length in the 15 min before P. Z only: the
horizontals carry long-period tilt drift that fakes events. If a quake that
should be louder here (bigger M - 1.66 log10(distance deg), the Ms distance
term) has waves arriving inside the window, the signal is credited to it and
this event is reported as masked, not registered. Registered events get
cmg3t/events/<origin UTC>_M<mag>_<place>/ with one PNG per band and event.txt;
every event is reported on one line either way (watch_cmg.py logs it).
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

import numpy as np
from scipy import signal

from loadOpts import load_config, ROOT

HERE = ROOT
OPTS = load_config(os.path.join(HERE, 'cmg3t.JSON'))
PI = 'david@%s' % OPTS['serverIP']
PI_DATA = '~/cmg3t/data'
LOCAL = os.path.join(HERE, 'data', 'cmg3t')
EVENTS = os.path.join(HERE, 'cmg3t', 'events')
STATION = (OPTS['stationLat'], OPTS['stationLon'])   # degrees, from cmg3t.JSON
SNR_MIN = 2.0
PRE_P = 300                                # s before P at the start of each plot
NOISE_SEC = 900                            # s before P used as the noise reference
RMS_WIN = {'SP': 10.0, 'MP': 30.0, 'LP': 120.0}   # length of a "stretch" per band
TAIL = 600                                 # s after the slowest surface waves
FS = 100.0
COLORS = {'Z': '#DC9257', 'N': '#6FB7D9', 'E': '#8CCB7E'}

# the three views of cmgclient.py: (key, title, low, high, decimate to, minimum window s)
SP, MP, LP = OPTS, OPTS.get('midPeriod', {}), OPTS.get('longPeriod', {})
BANDS = [('SP', 'short period', SP['cutLow'], SP['cutHigh'], 0, SP.get('plotSeconds', 900)),
         ('MP', '1-50 s', MP.get('cutLow', 0.02), MP.get('cutHigh', 1.0), MP.get('decimateTo', 5.0), 900),
         ('LP', '10-100 s', LP.get('cutLow', 0.01), LP.get('cutHigh', 0.1), LP.get('decimateTo', 2.0),
          LP.get('plotSeconds', 3600))]


def usgs_event(evid):
    u = 'https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&eventid=' + evid
    f = json.load(urllib.request.urlopen(u, timeout=30))
    p, (lon, lat, dep) = f['properties'], f['geometry']['coordinates']
    return dict(id=evid, mag=p['mag'], magType=p.get('magType', ''), place=p.get('place') or '',
                time=datetime.fromtimestamp(p['time'] / 1000, timezone.utc), lat=lat, lon=lon,
                depth=max(dep or 0, 0))


def usgs_window(t1, t2, minmag=4.0):
    u = ('https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&orderby=time-asc'
         '&minmagnitude=%g&starttime=%s&endtime=%s' % (minmag, t1.strftime('%Y-%m-%dT%H:%M:%S'),
                                                      t2.strftime('%Y-%m-%dT%H:%M:%S')))
    return [f['id'] for f in json.load(urllib.request.urlopen(u, timeout=30))['features']]


def strength(ev, deg):
    """Relative surface-wave size at the station: M - 1.66 log10(distance deg)."""
    return ev['mag'] - 1.66 * np.log10(max(deg, 0.5))


def masker(ev, deg, a, b):
    """Another catalogued quake that should be louder here and arrives within
    a..b s after ev's origin, or None."""
    t_o = ev['time']
    for oid in usgs_window(t_o - timedelta(hours=4), t_o + timedelta(seconds=b)):
        if oid == ev['id']:
            continue
        o = usgs_event(oid)
        odeg, _, oarr = arrivals(o)
        if strength(o, odeg) <= strength(ev, deg):
            continue
        shift = (o['time'] - t_o).total_seconds()
        o1, o2 = shift + (oarr['P'] or oarr['R1']), shift + oarr['R2'] + TAIL
        if o1 < b and o2 > a:
            return o
    return None


def arrivals(ev):
    """Seconds after origin: P, S (first arrivals, iasp91), surface waves 3.5 and 2.5 km/s."""
    for old, new in (('float_', np.float64), ('complex_', np.complex128)):   # obspy 1.4.1 taup vs NumPy 2
        if not hasattr(np, old):
            setattr(np, old, new)
    from obspy.geodetics import locations2degrees, degrees2kilometers
    from obspy.taup import TauPyModel
    deg = locations2degrees(ev['lat'], ev['lon'], *STATION)
    model = TauPyModel('iasp91')
    first = lambda phases: min((a.time for a in model.get_travel_times(ev['depth'], deg, phases)), default=None)
    p = first(['P', 'p', 'Pdiff', 'PKP', 'PKIKP'])
    s = first(['S', 's', 'Sdiff', 'SKS'])
    km = degrees2kilometers(deg)
    return deg, km, dict(P=p, S=s, R1=km / 3.5, R2=km / 2.5)


def pull(t1, t2):
    """scp the HH? hour files covering t1..t2 from the Pi into data/cmg3t/."""
    t = t1.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)   # files start ~1 min past the hour
    hours = []
    while t <= t2:
        hours.append(t)
        t += timedelta(hours=1)
    for day in sorted({h.strftime('%Y%m%d') for h in hours}):
        dst = os.path.join(LOCAL, day[:4], day)
        os.makedirs(dst, exist_ok=True)
        hs = [h for h in hours if h.strftime('%Y%m%d') == day]
        srcs = ['%s:%s/%s/%s/XX.QUAK..HH?.%s.mseed' % (PI, PI_DATA, day[:4], day, h.strftime('%Y%m%d_%H'))
                for h in hs]
        subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10'] + srcs + [dst],
                       capture_output=True, text=True)
    return hours


def load(t1, t2):
    from obspy import read, Stream, UTCDateTime
    st = Stream()
    t = t1.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    while t <= t2:
        d = t.strftime('%Y%m%d')
        for c in 'ZNE':
            f = os.path.join(LOCAL, d[:4], d, 'XX.QUAK..HH%s.%s.mseed' % (c, t.strftime('%Y%m%d_%H')))
            if os.path.exists(f):
                st += read(f)
        t += timedelta(hours=1)
    st.merge(method=1, fill_value='interpolate')
    st.trim(UTCDateTime(t1), UTCDateTime(t2))
    return st


def band(tr, lo, hi, dec, t0, t1):
    """Detrend, taper, zero-phase band-pass, decimate; returns (times rel. t0 [s], data)."""
    x = tr.data.astype(float)
    x = signal.detrend(x)
    x *= signal.windows.tukey(len(x), 0.05)
    sos = signal.butter(OPTS.get('filterOrder', 4), [lo, hi], 'bandpass', fs=FS, output='sos')
    y = signal.sosfiltfilt(sos, x)
    q = int(round(FS / dec)) if dec else 1
    y = y[::q]
    t = (tr.stats.starttime.timestamp - t0.timestamp()) + np.arange(len(y)) * q / FS
    keep = (t >= 0) & (t <= (t1 - t0).total_seconds())
    return t[keep], y[keep]


def loudest(t, y, a, b, win=10.0):
    """Largest RMS over `win` s inside a..b (s)."""
    sel = (t >= a) & (t < b)
    if sel.sum() < 4:
        return np.nan
    dt = t[1] - t[0]
    n = max(1, int(win / dt))
    p = np.convolve(y[sel] ** 2, np.ones(n) / n, mode='valid')
    return float(np.sqrt(p.max())) if len(p) else np.nan


def main(evid, force=False):
    if STATION == (0.0, 0.0):
        raise SystemExit("set stationLat / stationLon in cmg3t.JSON first")
    ev = usgs_event(evid)
    deg, km, arr = arrivals(ev)
    t_o = ev['time']
    p = arr['P'] or arr['R1']
    sig_end = arr['R2'] + TAIL
    tag = "M%.1f %s (%s) %.0f km / %.1f deg" % (ev['mag'], ev['place'], evid, km, deg)
    # data span: covers the longest view and leaves room for the 0.01 Hz filter to settle
    w1 = t_o + timedelta(seconds=p - NOISE_SEC - 600)
    w2 = t_o + timedelta(seconds=max(sig_end, p + 3600) + 600)
    pull(w1, w2)
    st = load(w1, w2)
    if len(st) < 3:
        print("NO DATA %s" % tag)
        return 2
    results, plots, windows = [], [], []
    for key, title, lo, hi, dec, minwin in BANDS:
        v1 = t_o + timedelta(seconds=p - PRE_P)
        v2 = max(t_o + timedelta(seconds=sig_end), v1 + timedelta(seconds=minwin))
        if key == 'SP':                           # short period: body waves, not hours of coda
            v2 = v1 + timedelta(seconds=max(minwin, min(sig_end, (arr['S'] or p) + 600) - p + PRE_P))
        traces = {}
        for c in 'ZNE':
            tr = st.select(channel='HH' + c)
            if tr:
                traces[c] = band(tr[0], lo, hi, dec, v1, v2)
        snr = 0.0
        z = st.select(channel='HHZ')
        if z:
            a, b = {'SP': (p, (arr['S'] or p) + 120), 'MP': (p, arr['R2'] + 300),
                    'LP': (arr['R1'] - 120, arr['R2'] + 300)}[key]
            t, y = band(z[0], lo, hi, dec, w1, w2)
            t -= (t_o - w1).total_seconds()            # seconds after origin
            noise = loudest(t, y, p - NOISE_SEC, p - 30, RMS_WIN[key])
            if key == 'SP':
                quake = max(loudest(t, y, ph - 10, ph + 120, RMS_WIN[key])
                            for ph in (p, arr['S'] or p))
            else:
                quake = loudest(t, y, a, b, RMS_WIN[key])
            snr = quake / noise if noise > 0 else 0.0
        results.append((key, snr))
        windows.append((a, b))
        plots.append((key, title, lo, hi, v1, v2, traces, snr))
    best = max(s for _, s in results)
    registered = best >= SNR_MIN
    summary = "  ".join("%s %.1f" % r for r in results)
    masked = None
    if registered:
        hits = [w for (k, sn), w in zip(results, windows) if sn >= SNR_MIN]
        masked = masker(ev, deg, min(a for a, _ in hits), max(b for _, b in hits))
        registered = masked is None
    state = ("REGISTERED" if registered else
             "masked by M%.1f %s (%s):" % (masked['mag'], masked['place'], masked['id']) if masked else "not seen  ")
    print("%s %s  SNR %s" % (state, tag, summary))
    if not (registered or force):
        return 1
    place = re.sub(r'[^A-Za-z0-9]+', '_', ev['place']).strip('_')[:40]
    out = os.path.join(EVENTS, '%s_M%.1f_%s' % (t_o.strftime('%Y%m%dT%H%M%SZ'), ev['mag'], place))
    os.makedirs(out, exist_ok=True)
    for key, title, lo, hi, v1, v2, traces, snr in plots:
        path = os.path.join(out, 'cmg3t_%s_%g-%gHz.png' % (key, lo, hi))
        draw(ev, deg, km, arr, key, title, lo, hi, v1, traces, snr, path)
        print("wrote", path)
    with open(os.path.join(out, 'event.txt'), 'w', encoding='utf-8') as fh:
        fh.write("USGS %s: M%.1f %s %s\norigin %s UTC  lat %.3f lon %.3f depth %.0f km\n"
                 "distance %.0f km (%.1f deg)\n" % (evid, ev['mag'], ev['magType'], ev['place'],
                                                     t_o.isoformat(), ev['lat'], ev['lon'], ev['depth'], km, deg))
        for k, v in arr.items():
            if v is not None:
                fh.write("%-3s %s UTC (+%.0f s)\n" % (k, (t_o + timedelta(seconds=v)).strftime('%H:%M:%S'), v))
        fh.write("SNR (Z, loudest stretch in the expected window / in the 15 min before P): %s\n" % summary)
    print("event folder:", out)
    return 0


def draw(ev, deg, km, arr, key, title, lo, hi, v1, traces, snr, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(3, 1, figsize=(OPTS.get('figW', 15), 9), sharex=True)
    t_o = ev['time']
    off = (t_o - v1).total_seconds()             # origin, in plot seconds
    for ax, c in zip(axs, 'ZNE'):
        if c in traces:
            t, y = traces[c]
            ax.plot(t / 60, y, lw=OPTS.get('plotLineW', 0.4) * 1.5, color=COLORS[c])
            lim = np.percentile(np.abs(y), 99.9) * 1.15 or 1
            ax.set_ylim(-lim, lim)
        for name, v, ls in (('P', arr['P'], '-'), ('S', arr['S'], '-'), ('R 3.5', arr['R1'], '--'),
                            ('R 2.5', arr['R2'], '--')):
            if v is not None and 0 <= off + v <= t[-1]:
                ax.axvline((off + v) / 60, color='k', lw=0.8, ls=ls, alpha=0.6)
                if c == 'Z':
                    ax.text((off + v) / 60, ax.get_ylim()[1], ' ' + name, va='top', fontsize=8)
        ax.set_ylabel('HH%s  nm/s' % c)
        ax.grid(alpha=0.25)
    axs[-1].set_xlabel('minutes after %s UTC (origin at %.1f min)' % (v1.strftime('%Y-%m-%d %H:%M:%S'), off / 60))
    fig.suptitle("CMG-3T %s view, %g-%g Hz  |  M%.1f %s  |  %s UTC, %.0f km deep  |  %.0f km (%.1f deg)  |  SNR %.1f"
                 % (title, lo, hi, ev['mag'], ev['place'], t_o.strftime('%Y-%m-%d %H:%M:%S'), ev['depth'],
                    km, deg, snr), fontsize=OPTS.get('titleFontSize', 9) + 1)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], '--force' in sys.argv))
