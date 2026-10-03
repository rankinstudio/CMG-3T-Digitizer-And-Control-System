# CMG-3T Digitizer And Control System

![The board box: VIN, AUX, ADC and VOUT on the front, the Pi on its side](images/box-front.jpg)

A three-component digitizer for a Güralp CMG-3T 120 s broadband seismometer:
three ADS1220s for velocity and an ADS1115 for the mass positions, on two
ElectroCookie solderable breadboards, read by a Raspberry Pi 5 that also
locks, unlocks and centres the masses. 0.489 nm/s per count, ±4.1 mm/s full
scale, ~1.5 nm/s/√Hz floor, 100 SPS miniSEED.

**`cmg3t-digitizer.html`** is the build document: block diagram, parts list
with DigiKey numbers, both board layouts hole by hole, the sensor cable table,
the Pi wiring, and the assembly and test stages.
**[Read it here](https://rankinstudio.github.io/CMG-3T-Digitizer-And-Control-System/cmg3t-digitizer.html)** (GitHub's file view shows only
the source).

Companion project: [DIY-SEISMO](https://github.com/rankinstudio/DIY-SEISMO), a
geophone station built the same way; the shared stream format and capture core
come from it.

| Path | What |
|---|---|
| `cmg3t-digitizer.html` | Build document |
| `connector_pinface.png` | The sensor's 26-pin connector, pin letters from the pin face |
| `pi/` | Pi software: capture daemon, mass control, bring-up checks ([README](pi/README.md)) |
| `enclosure/` | 3D-printed box for the boards with the Pi on its side ([README](enclosure/README.md)) |
| `pc/` | PC viewer (Z/N/E traces and spectrograms in three bands) and earthquake plots ([README](pc/README.md)) |

## Order of work

1. Build and test the ADC board, then the AUX board (build document, stages 1–2).
2. Print the fit test, then the box; fit the boards and the Pi (stage 3).
3. Pi: Raspberry Pi OS, `pcie_aspm=off`, the capture service (`pi/README.md`).
4. Sensor: harness buzzed out, connect it locked, power, unlock on its final
   pad (stage 4). Allow ~4 hours of settling.

## Earthquake plots on the PC

`watch_cmg.py` polls the USGS catalogue for M > 4 quakes, waits until their
waves have reached the Pi's disk, and runs `cmgevent.py <usgs id>`, which saves
Z/N/E plots in the viewer's three bands (0.5–2 Hz, 0.02–1 Hz, 0.01–0.1 Hz) when
the quake shows above the noise on Z. Set `stationLat` / `stationLon` in
`pc/cmg3t.JSON` first. Any quake by hand: `python cmgevent.py us6000tz62`.

## Photos

| | |
|---|---|
| ![Inside the box](images/box-inside.jpg) | ![The Pi on the side wall](images/pi-mount.jpg) |
| Inside: ADC board above the AUX board; keystones and DC jacks on the front wall | The Pi 5 on the side wall, GPIO wires over the top |
| ![The sensor](images/sensor.jpg) | |
| The CMG-3T: Dupont sockets on its pins, the two Ethernet cables and the power lead | |
