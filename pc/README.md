# PC software

Python 3 with `numpy`, `scipy`, `matplotlib` and `obspy`; `ssh`/`scp` with a key
to the Pi (`serverIP` in `cmg3t.JSON`, user `david` in `cmgevent.py`).

- `cmgclient.py` (settings `cmg3t.JSON`): live Z, N, E traces with spectrograms,
  in three views:

  | view | band | window | shows |
  |---|---|---|---|
  | short period | 0.5–2 Hz | 15 min | local and regional P and S, traffic |
  | 1–50 s | 0.02–1 Hz | 1 h | the ocean microseism, regional quakes |
  | 10–100 s | 0.01–0.1 Hz | 1 h | surface waves of distant quakes, below the microseism |

  Options: a host name or address; `--fresh` (ignore the Pi's replay, start
  empty); `--mp` or `--lp` (start in the 1–50 s or 10–100 s view); `--spec ZNE`
  (spectrograms for these components; `Z` is the default, `none` for traces only).
  Keys in the plot window:

  | key | does |
  |---|---|
  | `l` | next view: short period → 1–50 s → 10–100 s → short period |
  | `c` | clear the display and start again from the next block |
  | `s` | save the window as a PNG |
  | `f` | full screen on / off |
  | `g` | grid on / off (axes under the mouse) |
  | `q` | close |

  Each view's band, window length, decimation and spectrogram settings are in
  `cmg3t.JSON`: the top level is the short-period view, `midPeriod` and
  `longPeriod` override it for the other two.
- `cmgevent.py <usgs id>`: pulls the hours around a catalogued quake from the
  Pi, band-passes Z/N/E in those three bands, and saves the plots under
  `cmg3t/events/<origin>_M<mag>_<place>/` if the quake registered: Z louder,
  where that band's waves should arrive, than 2× anything in the 15 min before
  P. A quake whose window holds the waves of one that should be louder at the
  station is reported as masked. `--force` saves the plots regardless.
- `watch_cmg.py`: polls the USGS catalogue every 5 min for M > 4 and runs
  `cmgevent.py` for each once its waves have reached the Pi (up to ~2 h).
  Log to a file and leave it running, e.g. on Windows:

```powershell
New-Item -ItemType Directory -Force cmg3t\events | Out-Null
Start-Process python -ArgumentList '-u','-W','ignore','watch_cmg.py' -WindowStyle Hidden -RedirectStandardOutput cmg3t\events\watch.log -RedirectStandardError cmg3t\events\watch.err
```

Set `stationLat` / `stationLon` in `cmg3t.JSON` before using either.
