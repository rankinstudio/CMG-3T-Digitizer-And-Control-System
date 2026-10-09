"""Earthquake alert for cmgclient.py: rsudp's STA/LTA trigger with a timer, and its Telegram poster.

Settings under "alert" and "telegram" in cmg3t.JSON, with rsudp's names and meanings:

alert: the Z trace, one window of lta seconds ending at the newest sample, is
demeaned, given rsudp's velocity pre-filter (cosine taper 0.1-0.6 Hz, "deconvolve"
true; the 3T is flat, so that taper is all its response removal does), filtered
highpass-lowpass (4-pole Butterworth, one pass) and run through obspy's
recursive_sta_lta(sta, lta). As in rsudp the ratio starts from zero at the window's
start, so steady noise reads ~1.6 rather than 1. The trigger fires once the ratio
has stayed above "threshold" for "duration" seconds and resets when it falls
below "reset". It runs on every new Z block, whichever view is on screen.

On a trigger: a Telegram message, a start line on the traces (an end line on
reset), and if "eq_screenshots", a PNG of each view in "screenshot_views" (SP
short period, MP 1-50 s, LP 10-100 s), whichever view is on screen, each once the
event is "save_pct" of the way across that view's window (SP 0.7 x 15 min = 10.5
min after, MP and LP 0.7 x 1 h = 42 min, time for a distant quake's surface waves),
saved in "output_dir" and sent to Telegram if "send_images".
"""
import asyncio
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone

import numpy as np
import telegram as tg
from obspy.signal.filter import bandpass, highpass, lowpass
from obspy.signal.invsim import cosine_sac_taper
from obspy.signal.trigger import recursive_sta_lta

from loadOpts import ROOT

ALERT_DEFAULTS = {
    "enabled": True,
    "channel": "Z",
    "sta": 15,
    "lta": 140,
    "duration": 32.0,
    "threshold": 2.4,
    "reset": 1.6,
    "highpass": 0.01,
    "lowpass": 2,
    "deconvolve": True,
    "on_plot": True,
    "on_plot_end_line_color": "#D72638",
    "on_plot_start_line_color": "#4C8BF5",
    "eq_screenshots": True,
    "save_pct": 0.7,
    "screenshot_views": ["SP", "MP"],
    "output_dir": "cmg3t/alerts",
}
TELEGRAM_DEFAULTS = {
    "enabled": False,
    "send_images": True,
    "token": "",
    "chat_id": "",
    "extra_text": "",
    "upload_timeout": 30,
}
LIVE_SEC = 30          # only judge data this fresh: the Pi's replay on connect is history, not news


def _log(msg):
    print(datetime.now().strftime('%H:%M:%S ') + msg, flush=True)


def _utc(t):
    return datetime.fromtimestamp(t, timezone.utc)


class Telegrammer(threading.Thread):
    """rsudp's c_telegram.Telegrammer: a fresh Bot per send, sendMessage/sendPhoto awaited under
    asyncio.run, one retry after 5 s. One thread per chat id, as rsudp's client.py starts them."""

    def __init__(self, cfg, station, chat_id=None):
        super().__init__(daemon=True)
        self.cfg = dict(TELEGRAM_DEFAULTS, **cfg)
        self.token = self.cfg['token']
        self.chat_id = chat_id if chat_id is not None else str(self.cfg['chat_id']).strip()
        self.send_images = self.cfg['send_images']
        self.upload_timeout = self.cfg['upload_timeout']
        self.sender = 'Telegram id %s' % self.chat_id
        self.fmt = '%Y-%m-%d %H:%M:%S.%f'
        extra = self.cfg['extra_text']
        self.extra_text = ' %s' % str(extra)[:4096 - 177] if extra else ''
        self.message0 = '(CMG-3T station %s) Event detected at' % station
        self.queue = queue.Queue()
        _log('%s: Starting.' % self.sender)
        if not hasattr(tg, 'Bot'):
            # not python-telegram-bot: the PyPI 'telegram' stub, or a leftover empty telegram/ folder
            _log('%s: WARNING - %s imported "telegram" from %s, which has no Bot. '
                 'Run cmgclient in the Python env rsudp uses, or: pip install python-telegram-bot'
                 % (self.sender, sys.executable, getattr(tg, '__file__', None) or list(getattr(tg, '__path__', []))))

    def alarm(self, t):
        self.queue.put(('ALARM', t))

    def image(self, path):
        self.queue.put(('IMGPATH', path))

    def auth(self):
        self.telegram = tg.Bot(token=self.token)

    async def _when_alarm(self, t):
        message = '%s %s UTC%s' % (self.message0, _utc(t).strftime(self.fmt)[:22], self.extra_text)
        try:
            _log('%s: Sending alert...' % self.sender)
            self.auth()
            _log('%s: Telegram message: %s' % (self.sender, message))
            await self.telegram.sendMessage(chat_id=self.chat_id, text=message)
        except Exception as e:
            _log('%s: Could not send alert - %s' % (self.sender, e))
            try:
                _log('%s: Waiting 5 seconds and trying to send again...' % self.sender)
                time.sleep(5)
                self.auth()
                _log('%s: Telegram message: %s' % (self.sender, message))
                await self.telegram.sendMessage(chat_id=self.chat_id, text=message)
            except Exception as e:
                _log('%s: Could not send alert - %s' % (self.sender, e))

    async def _when_img(self, imgpath):
        if not self.send_images:
            return
        if not os.path.exists(imgpath):
            _log('%s: Could not find image: %s' % (self.sender, imgpath))
            return
        with open(imgpath, 'rb') as image:
            try:
                self.auth()
                _log('%s: Uploading image to Telegram %s' % (self.sender, imgpath))
                await self.telegram.sendPhoto(chat_id=self.chat_id, photo=image,
                                              write_timeout=self.upload_timeout, read_timeout=self.upload_timeout)
                _log('%s: Sent image' % self.sender)
            except Exception as e:
                _log('%s: Could not send image - %s' % (self.sender, e))
                try:
                    _log('%s: Waiting 5 seconds and trying to send again...' % self.sender)
                    time.sleep(5.1)
                    image.seek(0)
                    self.auth()
                    _log('%s: Uploading image to Telegram (2nd try) %s' % (self.sender, imgpath))
                    await self.telegram.sendPhoto(chat_id=self.chat_id, photo=image,
                                                  write_timeout=self.upload_timeout, read_timeout=self.upload_timeout)
                    _log('%s: Sent image' % self.sender)
                except Exception as e:
                    _log('%s: Could not send image - %s' % (self.sender, e))

    def run(self):
        while True:
            kind, arg = self.queue.get()
            if kind == 'ALARM':
                asyncio.run(self._when_alarm(arg))
            elif kind == 'IMGPATH':
                # rsudp bounds the upload by upload_timeout; allow for its 5 s retry as well
                try:
                    asyncio.run(asyncio.wait_for(self._when_img(arg), 2 * self.upload_timeout + 10))
                except asyncio.TimeoutError as e:
                    _log('%s: Asyncio timeout error - %s' % (self.sender, e))


def telegrammers(cfg, station):
    """One Telegrammer per comma-separated chat id, like rsudp's client.py."""
    return [Telegrammer(cfg, station, c) for c in str(cfg['chat_id']).strip(' ').split(',') if c.strip()]


class Alert(threading.Thread):
    """rsudp's c_alert with "duration" set: STA/LTA over the last lta seconds, a timer, a reset level.

    rx is the Receiver of the alert channel. The plot reads .ratio, .exceed, .timer,
    .events, .starts, .ends and pops .to_save; the trigger itself never waits on the plot.
    """

    def __init__(self, cfg, rx, telegram=()):
        super().__init__(daemon=True)
        self.cfg = dict(ALERT_DEFAULTS, **cfg)
        self.rx = rx
        self.telegram = [telegram] if isinstance(telegram, Telegrammer) else list(telegram or [])
        self.ratio = None
        self.exceed = False
        self.timer_start = None
        self.max_ratio = 0.0
        self.events = 0
        self.starts, self.ends = [], []          # epoch seconds of alarms and resets
        self.to_save = []                        # (alarm epoch, view) waiting for their screenshot
        self.lock = threading.Lock()
        out = self.cfg['output_dir']
        self.output_dir = out if os.path.isabs(out) else os.path.join(ROOT, out)

    def timer(self):
        """Seconds the ratio has been above threshold without yet triggering, or None."""
        return time.time() - self.timer_start if self.timer_start is not None else None

    def stalta(self, x, fs):
        c = self.cfg
        x = x - x.mean()
        if c['deconvolve']:
            # remove_response(pre_filt=[0.1, 0.6, 0.95 fs, fs], zero_mean=True, taper=False), flat response
            n = x.size
            nfft = 1 << int(np.ceil(np.log2(2 * n)))
            freqs = np.fft.rfftfreq(nfft, 1 / fs)
            x = np.fft.irfft(np.fft.rfft(x, nfft) * cosine_sac_taper(freqs, (0.1, 0.6, 0.95 * fs, fs)), nfft)[:n]
        lo, hi = c['highpass'], c['lowpass']
        if lo > 0 and hi < fs / 2:
            x = bandpass(x, lo, hi, df=fs, corners=4, zerophase=False)
        elif lo > 0:
            x = highpass(x, lo, df=fs, corners=4, zerophase=False)
        elif hi < fs / 2:
            x = lowpass(x, hi, df=fs, corners=4, zerophase=False)
        return recursive_sta_lta(x, int(c['sta'] * fs), int(c['lta'] * fs))

    def step(self, t_end, ratio):
        c = self.cfg
        self.ratio = ratio
        over = ratio > c['threshold']
        if self.exceed:
            self.max_ratio = max(self.max_ratio, ratio)
        if not self.exceed and over:
            if self.timer_start is None:
                self.timer_start = time.time()
            elif time.time() - self.timer_start > c['duration']:
                self.timer_start = None
                self.exceed = True
                self.max_ratio = ratio
                self._activate(t_end)
        else:
            self.timer_start = None
            if self.exceed and ratio < c['reset']:
                self.exceed = False
                self._deactivate(t_end)

    def _activate(self, t):
        c = self.cfg
        with self.lock:
            self.events += 1
            self.starts.append(t)
            if c['eq_screenshots']:
                self.to_save += [(t, v) for v in c['screenshot_views']]
        _log('ALERT  trigger threshold of %s exceeded for %s s at %s UTC (event %d)'
             % (c['threshold'], c['duration'], _utc(t).strftime('%Y-%m-%d %H:%M:%S.%f')[:22], self.events))
        _log('ALERT  will reset when STA/LTA goes below %s' % c['reset'])
        for tgm in self.telegram:
            tgm.alarm(t)

    def _deactivate(self, t):
        with self.lock:
            self.ends.append(t)
        _log('ALERT  max STA/LTA in alarm state %.3f; reset at %s UTC'
             % (self.max_ratio, _utc(t).strftime('%Y-%m-%d %H:%M:%S.%f')[:22]))

    def run(self):
        c = self.cfg
        _log('Alert: sta=%ss, lta=%ss, duration=%ss, threshold=%s, reset=%s, %s-%s Hz on %s'
             % (c['sta'], c['lta'], c['duration'], c['threshold'], c['reset'], c['highpass'], c['lowpass'],
                c['channel']))
        last = None
        while True:
            time.sleep(0.25)
            tail = self.rx.tail(c['lta'])
            if tail is None:
                continue
            t_end, x, fs = tail
            if t_end == last or time.time() - t_end > LIVE_SEC:
                continue
            last = t_end
            self.step(t_end, float(self.stalta(x, fs)[-1]))

    def screenshots_due(self, plot_seconds):
        """(alarm epoch, view) screenshots due now: the event save_pct across that view's plot_seconds[view]."""
        now = time.time()
        with self.lock:
            due = [(t, v) for t, v in self.to_save if now - t >= self.cfg['save_pct'] * plot_seconds[v]]
            self.to_save = [e for e in self.to_save if e not in due]
        return due

    def screenshot_path(self, station, t, view):
        os.makedirs(self.output_dir, exist_ok=True)
        return os.path.join(self.output_dir, '%s-%s-%s.png' % (station, _utc(t).strftime('%Y-%m-%d-%H%M%S'), view))

    def lines(self, t_left):
        with self.lock:
            return [s for s in self.starts if s >= t_left], [e for e in self.ends if e >= t_left]
