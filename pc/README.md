# PC software

Python 3 with `numpy`, `scipy`, `matplotlib`, `obspy` and `python-telegram-bot`; `ssh`/`scp` with a key
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

  Earthquake alert (`cmgalert.py`, `alert` and `telegram` in `cmg3t.JSON`, same
  names and meanings as rsudp's): a recursive STA/LTA on Z (15 s / 140 s,
  0.01–2 Hz after rsudp's 0.1–0.6 Hz velocity pre-filter) triggers once it has
  stayed above `threshold` (2.4) for `duration` (32 s), and resets below `reset`
  (1.6). As in rsudp the ratio starts from zero at the start of its 140 s
  window, so steady noise reads ~1.6, not 1. Each trigger posts a Telegram
  message, draws a blue start line (red at reset) on the traces, and saves the
  views in `screenshot_views` (`SP`, `MP`, `LP`; default short period and
  1–50 s) to `cmg3t/alerts/`, whichever view is on screen, sending each PNG to
  the same chat. Each is saved once the event is `save_pct` (0.7) across that
  view's window: short period 10.5 min after the trigger, 1–50 s 42 min after,
  late enough for a distant quake's surface waves. It runs in
  every view; the title shows the current ratio, the timer while it runs, and
  the trigger count. Needs `pip install python-telegram-bot`.
- `cmgevent.py <usgs id>`: pulls the hours around a catalogued quake from the
  Pi, band-passes Z/N/E in those three bands, and saves the plots under
  `cmg3t/events/<origin>_M<mag>_<place>/` if the quake registered: Z louder,
  where that band's waves should arrive, than 2× anything in the 15 min before
  P. A quake whose window holds the waves of one that should be louder at the
  station is reported as masked. `--force` saves the plots regardless.
- `cmgwaves.py <usgs id>`: dark plots of one quake in the 1–50 s and 10–100 s
  bands, Z with N/E rotated to radial and transverse, every wave type labelled
  (P, PP, S, SS, SSS, Love, Rayleigh, coda, other quakes' P), plus a labelled
  Z / transverse spectrogram per band, into the same event folder. Uses the data
  `cmgevent.py` already pulled; `--pull` fetches it from the Pi first.
- `watch_cmg.py`: polls the USGS catalogue every 5 min for M > 4 and runs
  `cmgevent.py` for each once its waves have reached the Pi (up to ~2 h).
  Log to a file and leave it running, e.g. on Windows:

```powershell
New-Item -ItemType Directory -Force cmg3t\events | Out-Null
Start-Process python -ArgumentList '-u','-W','ignore','watch_cmg.py' -WindowStyle Hidden -RedirectStandardOutput cmg3t\events\watch.log -RedirectStandardError cmg3t\events\watch.err
```

Set `stationLat` / `stationLon` in `cmg3t.JSON` before using either.
