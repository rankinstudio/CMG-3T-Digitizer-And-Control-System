#!/usr/bin/env python3
"""Watch the USGS catalogue; save CMG-3T plots for every M > 4 quake that registers.

    python watch_cmg.py

Every POLL s it asks USGS for quakes above MIN_MAG in the last LOOKBACK hours
(so magnitudes revised upward still get picked up, and a restart catches up).
Each new one waits until its slowest waves plus the long-period plot window
have passed and reached the Pi's disk, then cmgevent.py decides whether it
registered and, if so, writes cmg3t/events/<origin>_M<mag>_<place>/ with the
short-period, 1-50 s and 10-100 s plots. One log line per event either way.
Ids already handled are kept in cmg3t/events/done.txt.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.realpath(__file__))
EVENTS = os.path.join(HERE, 'cmg3t', 'events')
DONE = os.path.join(EVENTS, 'done.txt')
MIN_MAG = 4.0                    # strictly greater than
POLL = 300
LOOKBACK = 6                     # hours
SETTLE = 300                     # s after the last needed sample before pulling (hourly files flush every 60 s)


def say(msg):
    print(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S ') + msg, flush=True)


def recent():
    t1 = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK)
    u = ('https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&orderby=time-asc'
         '&minmagnitude=%g&starttime=%s' % (MIN_MAG, t1.strftime('%Y-%m-%dT%H:%M:%S')))
    feats = json.load(urllib.request.urlopen(u, timeout=30))['features']
    return [f for f in feats if (f['properties']['mag'] or 0) > MIN_MAG]


def due_time(f):
    """When cmgevent.py can run: its data span ends at max(R 2.5 km/s + tail, P + 1 h) + 10 min."""
    import cmgevent as ce
    ev = ce.usgs_event(f['id'])
    deg, km, arr = ce.arrivals(ev)
    p = arr['P'] or arr['R1']
    end = max(arr['R2'] + ce.TAIL, p + 3600) + 600
    return ev, ev['time'] + timedelta(seconds=end + SETTLE)


RUN = threading.Lock()           # one cmgevent.py at a time: they share the data/cmg3t files


def handle(f, done, lock, queued):
    try:
        ev, due = due_time(f)
    except Exception as e:
        say("could not set up %s: %s" % (f['id'], e))
        return
    say("M%.1f %s (%s) at %s UTC; checking at %s UTC"
        % (ev['mag'], ev['place'], f['id'], ev['time'].strftime('%H:%M:%S'), due.strftime('%H:%M')))
    wait = (due - datetime.now(timezone.utc)).total_seconds()
    if wait > 0:
        time.sleep(wait)
    with RUN:
        r = subprocess.run([sys.executable, '-W', 'ignore', os.path.join(HERE, 'cmgevent.py'), f['id']],
                           capture_output=True, text=True, cwd=HERE)
    for line in ((r.stdout or '') + (r.stderr or '')).strip().splitlines():
        if not line.startswith('wrote '):
            say("  " + line)
    with lock:
        done.add(f['id'])
        with open(DONE, 'a') as fh:
            fh.write(f['id'] + '\n')


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    os.makedirs(EVENTS, exist_ok=True)
    done = set(open(DONE).read().split()) if os.path.exists(DONE) else set()
    queued, lock = set(), threading.Lock()
    say("watching USGS for M > %g (polling every %d s, %d h look-back, %d already done)"
        % (MIN_MAG, POLL, LOOKBACK, len(done)))
    down = False
    while True:
        try:
            feats = recent()
            if down:
                say("USGS reachable again"); down = False
        except Exception as e:
            if not down:
                say("USGS query failed (%s); will keep trying" % e); down = True
            feats = []
        for f in feats:
            if f['id'] not in done and f['id'] not in queued:
                queued.add(f['id'])
                threading.Thread(target=handle, args=(f, done, lock, queued), daemon=True).start()
        time.sleep(POLL)


if __name__ == '__main__':
    main()
