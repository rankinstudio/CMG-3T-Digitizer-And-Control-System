# CMG-3T station software (Raspberry Pi 5, `~/cmg3t`)

| File | What |
|---|---|
| `cmgcapture.py` | Capture daemon: 3 × ADS1220 → 100 SPS on the UTC grid → miniSEED + TCP; mass positions at 1 SPS |
| `cmgctl.py` | `mass [secs]`, `lock`, `unlock`, `centre`: 7 s pulse on the optocoupler, then watches the masses |
| `config.JSON` | Station identity, ports, log retention |
| `cmgcapture.service` | systemd unit |
| `aux_check.py`, `ads_check.py`, `ads_noise.py` | Bring-up checks |
| `geocapture.py`, `geostream.py`, `loadOpts.py` | Shared core from DIY-SEISMO: timing, miniSEED writer, network, settings |

Copy the whole folder to `~/cmg3t` on the Pi. Python: a venv with system site packages plus
`obspy` and `smbus2` (`gpiod` and `spidev` come with Raspberry Pi OS).

## Pi 5: turn PCIe ASPM off

Append ` pcie_aspm=off` to the single line in `/boot/firmware/cmdline.txt` and
reboot. With ASPM on, the PCIe link to the RP1 (the chip behind the GPIO and SPI
pins) drops into L1 between reads and puts an ~80 ms disturbance on all three
channels every 2.77 s. Check: `cat /proc/cmdline` shows `pcie_aspm=off`.

## Running

    sudo cp cmgcapture.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now cmgcapture
    journalctl -u cmgcapture -f

It waits for NTP before it starts logging. Stop it before `ads_check.py` or
`ads_noise.py`; `cmgctl.py` and `aux_check.py` run alongside it.

## Mass control

    python3 cmgctl.py mass        # positions once; centred is within ±2 V, locked ±8 V
    python3 cmgctl.py unlock      # only level, on the final pad
    python3 cmgctl.py centre      # if a mass settles beyond ±2 V
    python3 cmgctl.py lock        # before moving it. Never move it unlocked

Each command takes a few minutes of audible motor activity. After unlocking,
allow about 4 hours of settling before trusting the long-period band.

## Output

- `~/cmg3t/data/YYYY/YYYYMMDD/XX.QUAK..HH{Z,N,E}.YYYYMMDD_HH.mseed`: FLOAT32 nm/s,
  exactly 100 SPS, raw (no DC block or high-pass)
- `...LM{Z,N,E}...mseed`: mass positions, volts, 1 SPS
- TCP, GEO1 format: Z 1244, N 1245, E 1246; the last hour is replayed on connect
- Pruned at 365 days / 40 GB

## Status line (journal, every 10 s)

`150.00 SPS  seg 0  missed 0  late 0 (max 0.01 ms)  clients 3  queued 0  peak … nm/s  mass +0.11 -0.71 +0.28 V`

- seg: timing segments; a new one is a stall or clock step (a gap in the data)
- missed / late: conversions not read in time, filled by interpolation
- peak: largest |velocity| in the last 10 s per component
- mass: a warning is logged past ±2.5 V (centre) or ±7 V (on the stops)

## Troubleshooting

- All three masses read the same value (e.g. +0.67 V) and the three velocity
  channels wander together: the sensor has no power. Check its two-core lead
  (c / b) and the VIN / VOUT jacks.
- All three masses read about +2.5 V (the open-input value): the CTL cable is
  not plugged in.

## Viewing on a PC

    python cmgclient.py            # Z/N/E traces + spectrograms; l cycles the views

From `../pc`; settings in `pc/cmg3t.JSON`.
