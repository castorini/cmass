"""Shared utilities for the corpus analysis pipeline.

Doc id format is ``shard_{shard:05d}_{row:05d}`` (e.g. ``shard_00188_76292``) -- fixed
width, 17 bytes, lexicographically sortable, and self-describing: the shard is encoded in
the id, so nothing downstream needs a (doc_id, file) tuple.

A corpus of this size (553M documents for ClimbMix) means anything touching every document
is vectorised with numpy rather than looped in Python.  In particular the doc-id and
hex-digest formatters below build ASCII directly into preallocated uint8 buffers.
"""

import json
import os
import re

import numpy as np

from config import CORPUS_DIR, OUT_DIR          # noqa: F401  (re-exported)

SHARD_RE = re.compile(r"^shard_(\d{5})\.parquet$")

DOC_ID_LEN = 17  # len("shard_00188_76292")
DIGEST_LEN = 32  # sha256


# --------------------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------------------

def discover_shards(corpus_dir=CORPUS_DIR):
    """Return [(shard_num, path)] sorted by the number parsed from the *filename*.

    Deliberately keyed off the filename rather than the position in a directory listing,
    so a missing shard can never silently renumber every doc id after it.
    """
    out = []
    for name in os.listdir(corpus_dir):
        m = SHARD_RE.match(name)
        if m:
            out.append((int(m.group(1)), os.path.join(corpus_dir, name)))
    out.sort()
    if not out:
        raise RuntimeError(f"no shard_NNNNN.parquet files under {corpus_dir}")
    return out


def load_manifest(out_dir=OUT_DIR):
    with open(os.path.join(out_dir, "manifest.json")) as fh:
        man = json.load(fh)
    man["shard_nums"] = np.array([s["num"] for s in man["shards"]], dtype=np.int64)
    man["n_rows"] = np.array([s["n_rows"] for s in man["shards"]], dtype=np.int64)
    # offsets[i] = global index of row 0 of shard i; offsets[-1] = total_rows
    man["offsets"] = np.concatenate([[0], np.cumsum(man["n_rows"])])
    return man


def sidecar_paths(out_dir, shard_num):
    d = os.path.join(out_dir, "stage1")
    return (
        os.path.join(d, f"shard_{shard_num:05d}.len.npy"),
        os.path.join(d, f"shard_{shard_num:05d}.sha.npy"),
    )


def atomic_save(path, arr):
    """np.save via a .tmp sibling + os.replace, so a partial write is never mistaken for
    a finished shard by the resume logic."""
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        np.save(fh, arr, allow_pickle=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------------------
# vectorised ASCII formatting
# --------------------------------------------------------------------------------------

def _digit_table(n, width):
    """uint8[n, width] of zero-padded decimal ASCII for 0..n-1."""
    tbl = np.empty((n, width), dtype=np.uint8)
    vals = np.arange(n, dtype=np.int64)
    for pos in range(width - 1, -1, -1):
        tbl[:, pos] = (vals % 10) + 48
        vals //= 10
    if vals.any():
        raise ValueError(f"{n - 1} does not fit in {width} digits")
    return tbl


class DocIdFormatter:
    """Turns arrays of *global* row indices into ASCII doc ids, vectorised.

    ``json_array(gidx)`` returns e.g. ``b'"shard_00000_00001","shard_00002_00003"'`` --
    the inside of a JSON array, without the enclosing brackets.
    """

    # layout of one 20-byte element: '"shard_' NNNNN '_' RRRRR '",'
    _PRE = b'"shard_'          # bytes 0..6
    _SHARD_AT = 7              # bytes 7..11
    _SEP_AT = 12               # byte  12
    _ROW_AT = 13               # bytes 13..17
    _CLOSE_AT = 18             # bytes 18..19  ('",')
    _ELEM = 20

    def __init__(self, manifest):
        self.offsets = manifest["offsets"]
        self.shard_digits = _digit_table(int(manifest["shard_nums"].max()) + 1, 5)
        self.row_digits = _digit_table(int(manifest["n_rows"].max()), 5)
        self.shard_nums = manifest["shard_nums"]

    def split(self, gidx):
        """global index -> (shard_num, row_in_shard), both vectorised."""
        shard_idx = np.searchsorted(self.offsets, gidx, side="right") - 1
        row = gidx - self.offsets[shard_idx]
        return self.shard_nums[shard_idx], row

    def _fill(self, gidx):
        n = gidx.shape[0]
        buf = np.empty((n, self._ELEM), dtype=np.uint8)
        buf[:, : len(self._PRE)] = np.frombuffer(self._PRE, dtype=np.uint8)
        buf[:, self._SEP_AT] = ord("_")
        buf[:, self._CLOSE_AT] = ord('"')
        buf[:, self._CLOSE_AT + 1] = ord(",")
        shard_num, row = self.split(gidx)
        buf[:, self._SHARD_AT : self._SHARD_AT + 5] = self.shard_digits[shard_num]
        buf[:, self._ROW_AT : self._ROW_AT + 5] = self.row_digits[row]
        return buf

    def json_array(self, gidx):
        """Comma-separated, quoted doc ids -- no enclosing brackets, no trailing comma."""
        if gidx.shape[0] == 0:
            return b""
        return self._fill(gidx).tobytes()[:-1]  # drop the final comma

    def fixed_width(self, gidx):
        """numpy |S17 array of bare (unquoted) doc ids."""
        buf = self._fill(gidx)
        return np.ascontiguousarray(buf[:, 1 : 1 + DOC_ID_LEN]).view(f"|S{DOC_ID_LEN}").ravel()


_HEX_LUT = np.frombuffer(
    b"".join(b"%02x" % i for i in range(256)), dtype=np.uint8
).reshape(256, 2)


def hex_digests(digests):
    """uint8[n, 32] raw digests -> numpy |S64 array of lowercase hex."""
    n = digests.shape[0]
    out = _HEX_LUT[digests].reshape(n, DIGEST_LEN * 2)
    return np.ascontiguousarray(out).view("|S64").ravel()


# --------------------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------------------

def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024


def log(msg):
    import datetime
    import sys

    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True, file=sys.stderr)
