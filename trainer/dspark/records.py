"""Streaming reader for DS4_TRAIN_DUMP .recs files (see data_contract.py)."""
from __future__ import annotations

import mmap
import os
import struct
from dataclasses import dataclass

from .data_contract import (HEADER_BYTES, N_LABELS, RECORD_BYTES, RECORD_FMT,
                            FLAG_SEGMENT_START, STREAK_CLAMP)

_HDR = struct.Struct(RECORD_FMT)


@dataclass
class RecHeader:
    pos: int
    seed: int
    streak: int
    n_label: int
    n_feat: int
    flags: int
    labels: tuple

    @property
    def usable(self) -> bool:
        return self.n_label > 0

    @property
    def gap_for_stats(self) -> bool:
        return self.streak < STREAK_CLAMP


def read_headers(path: str):
    """Yield (record_index, RecHeader) for every record in a .recs file."""
    size = os.path.getsize(path)
    n = size // RECORD_BYTES          # trailing partial record: ignored by
    with open(path, "rb") as fd:      # validate.py owns framing checks
        for i in range(n):
            fd.seek(i * RECORD_BYTES)
            r = _HDR.unpack(fd.read(HEADER_BYTES))
            yield i, RecHeader(pos=r[0], seed=r[1], streak=r[2], n_label=r[3],
                               n_feat=r[4], flags=r[5], labels=r[6:11])


def feature_mmap(path: str):
    fd = open(path, "rb")
    mm = mmap.mmap(fd.fileno(), 0, access=mmap.ACCESS_READ)
    return fd, mm


def feature_bytes(mm, index: int, n_feat: int, width: int = 4096) -> bytes:
    start = index * RECORD_BYTES + HEADER_BYTES
    return mm[start:start + n_feat * width]
