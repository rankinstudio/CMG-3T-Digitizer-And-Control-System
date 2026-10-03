"""config.JSON loader. Every key has a default, so a missing one is not a crash."""
import json
import os

ROOT = os.path.dirname(os.path.realpath(__file__))

DEFAULTS = {
    # identity, used in file names and miniSEED headers
    "network": "XX",
    "station": "QUAK",
    "channel": "EHZ",

    # capture (Pi)
    "serverPort": 1243,
    "blockSec": 1.0,            # seconds per streamed block
    "logDir": "data",           # relative to this folder unless absolute
    "logFormat": "mseed",       # "mseed" (needs obspy) or "csv"; falls back to csv
    "logFlushSec": 60,          # seconds of samples per write to disk
    "historySec": 960,          # seconds replayed to a newly connected client (>= plotSeconds)
    "retainDays": 180,          # delete day folders older than this (0 = keep forever)
    "maxLogGB": 15,             # and oldest-first until the log is under this (0 = no cap)
    "idleSleepFrac": 0.6,       # fraction of the conversion period to sleep before spinning on DRDY

    # client (PC)
    "serverIP": "10.0.0.237",
    "plotSeconds": 900,
    "cutLow": 0,              # display band, Hz
    "cutHigh": 5.0,
    "filterOrder": 4,
    "specWindow": 256,          # spectrogram FFT length, samples
    "specMinHz": 0,             # bottom of the spectrogram axis
    "specHighpassHz": 0.3,      # 2-pole roll-off applied before the FFT (-6 dB here, -12 dB/oct
                                # below) so the 1/f floor fades out of the display instead of
                                # owning the palette. 0 = flat to 0.31 Hz
    "specMaxHz": 70,            # top of the spectrogram axis (capped at Nyquist)
    "specHopSec": 1.0,          # seconds between spectrogram columns (smaller = finer in time)
    "specSmoothSec": 0.0,       # running average over this many seconds of columns (0 = none)
    "specPad": 1,               # zero-pad the FFT to this multiple of specWindow: interpolates rows (4 = rsudp)
    "specScale": "db",          # "db", or "root" for rsudp's power**0.1 (damps speckle, loud things pop)
    "specWhiten": False,        # subtract each frequency's median so a hot band cannot own the palette
    "specRangePct": [5, 99.5],  # colour range: these percentiles of the displayed band
    "STA": 2.0,                 # seconds
    "LTA": 30.0,
    "staltaTrigger": 4.0,
    "plotLineW": 0.4,
    "figW": 11.8,
    "figH": 6.8,
    "titleFontSize": 9,
    "refreshMs": 1500,
}


def load_config(path=None):
    opts = dict(DEFAULTS)
    # GEOPH_CONFIG lets a test or a second instance point at another file
    path = path or os.environ.get('GEOPH_CONFIG') or os.path.join(ROOT, 'config.JSON')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            opts.update(json.load(f))
    if not os.path.isabs(opts['logDir']):
        opts['logDir'] = os.path.join(ROOT, opts['logDir'])
    return opts
