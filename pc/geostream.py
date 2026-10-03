"""Wire format shared by geocapture.py (Pi) and geoclient.py (PC).

One TCP stream, one direction, a sequence of self-describing blocks:

    magic   4 bytes   b'GEO1'
    t0      float64   UTC Unix time of the block's first sample
    fs      float64   sample rate the block was taken at, SPS (measured, not nominal)
    seg     uint32    timing segment number; increments when the capture re-anchors
                      its clock, so consecutive blocks with the same seg are contiguous
    n       uint32    number of samples
    data    n x float32   ground velocity in nm/s

All little-endian. No pickle: nothing on the wire is executable, a chunk
boundary can fall anywhere, and a reader can resynchronise on the magic.
"""
import socket
import struct
import time

import numpy as np

MAGIC = b'GEO1'
HEADER = struct.Struct('<4sddII')      # magic, t0, fs, seg, n
DEFAULT_PORT = 1243


def pack_block(t0, fs, seg, samples_nms):
    """Header + float32 payload for one block."""
    a = np.ascontiguousarray(samples_nms, dtype='<f4')
    return HEADER.pack(MAGIC, float(t0), float(fs), int(seg), a.size) + a.tobytes()


def recv_exact(sock, n):
    """Read exactly n bytes or raise ConnectionError."""
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        k = sock.recv_into(view[got:], n - got)
        if k == 0:
            raise ConnectionError("peer closed")
        got += k
    return bytes(buf)


def read_block(sock):
    """Next (t0, fs, seg, ndarray) from the socket. Resyncs on a bad magic."""
    hdr = recv_exact(sock, HEADER.size)
    while not hdr.startswith(MAGIC):
        # slide one byte at a time until the magic lines up again
        hdr = hdr[1:] + recv_exact(sock, 1)
    magic, t0, fs, seg, n = HEADER.unpack(hdr)
    if n > 1_000_000:
        raise ConnectionError("absurd block length %d -- stream corrupt" % n)
    data = np.frombuffer(recv_exact(sock, 4 * n), dtype='<f4')
    return t0, fs, seg, data


def connect(host, port=DEFAULT_PORT, retry=4.0, log=print):
    """Blocking connect that keeps trying. Returns a connected socket."""
    while True:
        try:
            s = socket.create_connection((host, port), timeout=10)
            s.settimeout(30)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            log("connected to %s:%d" % (host, port))
            return s
        except (OSError, UnicodeError) as e:      # UnicodeError: a hostname that is not one
            log("connect to %s:%d failed (%s); retry in %gs" % (host, port, e, retry))
            time.sleep(retry)
