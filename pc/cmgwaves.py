#!/usr/bin/env python3
"""Dark CMG-3T plots of one quake with each wave type labelled, Z / R / T.

    python cmgwaves.py us6000u18k            # a USGS event id, data already in data/cmg3t/
    python cmgwaves.py us6000u18k --pull     # scp the hours from the Pi first

Same data, bands and windows as cmgevent.py's MP and LP views, with N/E rotated
to R (radial, + away from the quake: Rayleigh with Z) and T (transverse: Love,
which Z never sees). Body waves are iasp91 times; surface-wave spans are
group-velocity windows over the path. Other M>=5 quakes whose P lands in the
window are marked. Each view also gets a labelled Z / T spectrogram
(spectrogram_*.png). PNGs go in the event's cmg3t/events/ folder (made if needed)."""
import os
import re
import sys
from datetime import timedelta

import numpy as np
from scipy import signal

import cmgevent as c

BG, FG, GRID = '#0F1216', '#D8DEE6', '#2A3038'
COL = dict(Z='#F0A060', R='#E8C35A', T='#C58BE0')
WAVE = dict(love='#C58BE0', rlong='#E8C35A', rtrain='#F07878', coda='#8A96A6', other='#5FA8F0')
BODY = ['P', 'PP', 'PPP', 'S', 'SS', 'SSS']
SPEC = {'MP': (4.0, 120, (1.5, 40)),     # spectrogram: rate Hz, window s, period range s
        'LP': (1.0, 300, (8, 150))}


def main(evid, pull=False):
    ev = c.usgs_event(evid)
    t_o = ev['time']
    deg, km, arr = c.arrivals(ev)            # also patches np.float_ for obspy taup
    from obspy.taup import TauPyModel
    from obspy.geodetics import gps2dist_azimuth
    baz = gps2dist_azimuth(c.STATION[0], c.STATION[1], ev['lat'], ev['lon'])[1]
    model = TauPyModel('iasp91')
    ph = {}
    for a in model.get_travel_times(ev['depth'], deg, BODY):
        ph.setdefault(a.name, a.time)
    p = arr['P'] or arr['R1']
    sig_end = arr['R2'] + c.TAIL

    views = []
    for key, title, lo, hi, dec, minwin in c.BANDS:
        if key == 'SP':
            continue
        v1 = t_o + timedelta(seconds=p - c.PRE_P)
        v2 = max(t_o + timedelta(seconds=sig_end), v1 + timedelta(seconds=minwin))
        views.append((key, title, lo, hi, dec, v1, v2))
    w1 = min(v[5] for v in views) - timedelta(seconds=1500)          # margin for the 0.01 Hz filter
    w2 = max(v[6] for v in views) + timedelta(seconds=600)
    if pull:                                   # off by default: watch_cmg.py may be reading the same files
        c.pull(w1, w2)
    st = c.load(w1, w2)
    if len(st) < 3:
        raise SystemExit('no data')

    # other quakes that should show up in the window: mark their P
    others = []
    for oid in c.usgs_window(t_o - timedelta(hours=2), w2, minmag=5.0):
        if oid == evid:
            continue
        o = c.usgs_event(oid)
        odeg, _, oarr = c.arrivals(o)
        if oarr['P']:
            others.append((o, (o['time'] - t_o).total_seconds() + oarr['P']))

    place = re.sub(r'[^A-Za-z0-9]+', '_', ev['place']).strip('_')[:40]
    out = os.path.join(c.EVENTS, '%s_M%.1f_%s' % (t_o.strftime('%Y%m%dT%H%M%SZ'), ev['mag'], place))
    os.makedirs(out, exist_ok=True)
    raw = {ch: st.select(channel='HH' + ch)[0] for ch in 'ZNE'}
    for key, title, lo, hi, dec, v1, v2 in views:
        tr = {}
        for ch in 'ZNE':
            t, tr[ch] = c.band(raw[ch], lo, hi, dec, v1, v2)
        t = t / 60 + (v1 - t_o).total_seconds() / 60                 # minutes after origin
        tr['R'], tr['T'] = rotate(tr['N'], tr['E'], baz)
        info = (ev, deg, km, baz, ph, others, title, lo, hi)
        path = os.path.join(out, 'waves_%s_%g-%gHz_dark.png' % (key, lo, hi))
        draw(info, t, tr, path)
        print('wrote', path)
        rawt = rotate(raw['N'].data.astype(float), raw['E'].data.astype(float), baz)[1]
        spec = {'Z': spectrogram(raw['Z'].data, raw['Z'].stats.starttime, key, t_o, t[0], t[-1]),
                'T': spectrogram(rawt, raw['N'].stats.starttime, key, t_o, t[0], t[-1])}
        path = os.path.join(out, 'spectrogram_%s_%g-%gHz_dark.png' % (key, lo, hi))
        draw_spec(info, key, t[0], t[-1], spec, path)
        print('wrote', path)


def rotate(n, e, baz):
    """N/E -> R (+ away from the quake), T."""
    b = np.radians(baz)
    return -(n * np.cos(b) + e * np.sin(b)), n * np.sin(b) - e * np.cos(b)


def spectrogram(data, start, key, t_o, x0, x1):
    """Power (dB) vs period: (minutes after origin, period s, dB)."""
    rate, win, prange = SPEC[key]
    x = signal.detrend(np.asarray(data, float))
    fs = c.FS
    while fs / rate >= 2:                    # anti-aliased decimation in steps of <= 10
        q = int(min(10, round(fs / rate)))
        x = signal.decimate(x, q, ftype='fir', zero_phase=True)
        fs /= q
    n = int(win * fs)
    f, tt, p = signal.spectrogram(x, fs, window='hann', nperseg=n, noverlap=int(n * 0.9),
                                  scaling='density')
    tm = (start.timestamp - t_o.timestamp() + tt) / 60
    per = 1 / np.maximum(f, 1e-9)
    ft = (f > 0) & (per >= prange[0]) & (per <= prange[1])
    kt = (tm >= x0) & (tm <= x1)
    return tm[kt], per[ft], 10 * np.log10(p[np.ix_(ft, kt)] + 1e-30)


def setup():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'text.color': FG, 'axes.labelcolor': FG,
                         'xtick.color': FG, 'ytick.color': FG, 'axes.edgecolor': GRID})
    return plt


def spans_for(km, x1):
    v = lambda kms: km / kms / 60            # group velocity -> minutes after origin
    return [  # (name, start, end, colour, channels, label row)
        ('Love', v(4.5), v(3.0), WAVE['love'], 'T', 0),
        ('Rayleigh, long periods first', v(4.1), v(3.5), WAVE['rlong'], 'ZR', 1),
        ('Rayleigh, dispersed train', v(3.5), v(2.5), WAVE['rtrain'], 'ZR', 0),
        ('coda (scattered surface waves)', v(2.5), x1, WAVE['coda'], 'ZRT', 1),
    ]


def label(ax, info, ch, x0, x1, shade=True, quake_labels=True):
    """Surface-wave spans, body-wave lines and other quakes' P on one panel. shade=False
    (spectrograms) draws the span edges instead of filling, so the colour map stays readable."""
    ev, deg, km, baz, ph, others = info[:6]
    span = x1 - x0
    for name, a, b, col, chans, row in spans_for(km, x1):
        if ch not in chans or b < x0 or a > x1:
            continue
        if shade:
            ax.axvspan(a, b, color=col, alpha=0.12, lw=0)
        else:
            for edge in (a, b):
                if x0 < edge < x1:
                    ax.axvline(edge, color=col, lw=1.1, ls='--', alpha=0.9)
        yb = 0.985 - 0.085 * row
        ax.annotate('', xy=(a, yb - 0.035), xytext=(min(b, x1), yb - 0.035),
                    xycoords=ax.get_xaxis_transform(),
                    arrowprops=dict(arrowstyle='<->', color=col, lw=1.2))
        ax.text(max(a, x0) + 0.3, yb, name, transform=ax.get_xaxis_transform(), ha='left', va='top',
                color=col, fontsize=9.5, fontweight='bold',
                bbox=dict(facecolor=BG, edgecolor='none', pad=1.0, alpha=0.8))
    last, row = -1e9, 0
    for name, s in sorted(ph.items(), key=lambda kv: kv[1]):
        m = s / 60
        if not x0 <= m <= x1:
            continue
        row = row + 1 if m - last < 0.025 * span else 0
        last = m
        ax.axvline(m, color=FG, lw=0.8, alpha=0.45 if shade else 0.7)
        ax.text(m, 0.03 + 0.06 * row, ' ' + name, transform=ax.get_xaxis_transform(), color=FG,
                fontsize=8.5, alpha=0.9, bbox=dict(facecolor=BG, edgecolor='none', pad=0.5, alpha=0.7))
    last, row = -1e9, 0
    for o, s in sorted(others, key=lambda x: x[1]):
        m = s / 60
        if not x0 <= m <= x1:
            continue
        row = row + 1 if m - last < 0.06 * span else 0
        last = m
        ax.axvline(m, color=WAVE['other'], lw=0.9, ls=':', alpha=0.8)
        if quake_labels:
            right = m > x0 + 0.9 * span
            ax.text(m, 0.30 - 0.07 * row, (' M%.1f P  %s ' if right else ' M%.1f P  %s')
                    % (o['mag'], o['time'].strftime('%H:%M')), ha='right' if right else 'left',
                    transform=ax.get_xaxis_transform(), color=WAVE['other'], fontsize=8,
                    bbox=dict(facecolor=BG, edgecolor='none', pad=0.5, alpha=0.7))
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.set_xlim(x0, x1)


def finish(fig, axs, info, path):
    ev, deg, km, baz = info[:4]
    axs[-1].set_xlabel('minutes after origin, %s UTC   (Love 4.5-3.0 km/s, Rayleigh 4.1-3.5 and 3.5-2.5 km/s;'
                       ' back azimuth %.0f°; dotted blue = P of other M5+ quakes, origin UTC)'
                       % (ev['time'].strftime('%Y-%m-%d %H:%M:%S'), baz), fontsize=9.5)
    fig.tight_layout()
    fig.savefig(path, dpi=120, facecolor=BG)


def draw(info, t, tr, path):
    plt = setup()
    ev, deg, km, baz, ph, others, title, lo, hi = info
    fig, axs = plt.subplots(3, 1, figsize=(16, 10), sharex=True, facecolor=BG)
    names = dict(Z='HHZ  vertical', R='R  radial (N/E rotated)', T='T  transverse (N/E rotated)')
    for ax, ch in zip(axs, 'ZRT'):
        ax.set_facecolor(BG)
        y = tr[ch]
        lim = np.percentile(np.abs(y), 99.9) * 1.3
        label(ax, info, ch, t[0], t[-1], quake_labels=ch == 'Z')
        ax.plot(t, y, lw=0.6, color=COL[ch])
        ax.set_ylim(-lim, lim)
        ax.set_ylabel('%s\nnm/s' % names[ch], fontsize=9.5)
        ax.grid(color=GRID, lw=0.6)
    fig.suptitle('CMG-3T  %s (%g-%g Hz)   M%.1f %s   %.0f km (%.1f°), %.0f km deep'
                 % (title, lo, hi, ev['mag'], ev['place'], km, deg, ev['depth']), fontsize=13, color=FG)
    finish(fig, axs, info, path)
    plt.close(fig)


def draw_spec(info, key, x0, x1, spec, path):
    plt = setup()
    ev, deg, km, baz, ph, others, title, lo, hi = info
    rate, win, prange = SPEC[key]
    fig, axs = plt.subplots(2, 1, figsize=(16, 10), sharex=True, facecolor=BG)
    names = dict(Z='HHZ  vertical: Rayleigh', T='T  transverse: Love')
    for ax, ch in zip(axs, 'ZT'):
        ax.set_facecolor(BG)
        tm, per, db = spec[ch]
        lo_db, hi_db = np.percentile(db, [5, 99.7])
        ax.pcolormesh(tm, per, db, shading='nearest', cmap='magma', vmin=lo_db, vmax=hi_db, rasterized=True)
        ax.set_yscale('log')
        ax.set_ylim(*prange)
        ticks = [p for p in (2, 5, 10, 20, 40, 100) if prange[0] <= p <= prange[1]]
        ax.set_yticks(ticks)
        ax.set_yticklabels([str(p) for p in ticks])
        ax.minorticks_off()
        ax.set_ylabel('%s\nperiod s' % names[ch], fontsize=10)
        label(ax, info, ch, x0, x1, shade=False, quake_labels=ch == 'Z')
    axs[0].text(0.995, 0.04, 'brighter = more power (dB, %d s windows). Surface waves: long periods arrive'
                ' first, then the period falls (dispersion)' % win, transform=axs[0].transAxes, ha='right',
                color=FG, fontsize=8.5, bbox=dict(facecolor=BG, edgecolor='none', pad=1.0, alpha=0.7))
    fig.suptitle('CMG-3T spectrogram, %s view   M%.1f %s   %.0f km (%.1f°), %.0f km deep'
                 % (title, ev['mag'], ev['place'], km, deg, ev['depth']), fontsize=13, color=FG)
    finish(fig, axs, info, path)
    plt.close(fig)


if __name__ == '__main__':
    main(sys.argv[1], '--pull' in sys.argv)
