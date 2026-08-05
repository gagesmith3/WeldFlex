"""Decoder for the FAIRINO controller's port-8083 status feed.

Pure functions and one incremental reader — no sockets, no threads, no clock.
`robot_feed.py` owns the transport; everything here can be exercised offline
against synthetic bytes, which is the whole point: the frame layout is the part
most likely to be wrong, and it is the part cheapest to test without a robot.

Wire format (Table 2-1 of `docs/collabrative_robot_8083_port_status.md`):

    0x5A5A | CNT:u8 | LEN:u16 | DATA[LEN] | CHECKSUM:u16

`CHECKSUM` is the sum of every byte from the frame header through the end of
DATA, truncated to 16 bits. Total frame size is therefore ``LEN + 7``.

Two decisions worth knowing about:

**Fields are decoded individually against an offset table, not with one big
`struct.unpack` format.** Firmware revisions append fields to the end of this
structure — the manual's own tables grew that way. A single unpack would raise
on any length mismatch and throw away all 76 fields over one trailing change.
Every signal WeldFlex needs lives below offset 420 of 650, so a short or long
DATA still yields usable telemetry. A field whose bytes are not present is
simply absent from the returned dict.

**Endianness is assumed little and then verified.** The manual never states it.
`looks_sane()` exists so the caller can prove the assumption on the first frame
rather than publishing plausible-looking garbage — several fields have documented
small ranges, and a wrong-endian float64 is never a believable joint angle.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any, Iterator

HEADER = 0x5A5A
HEADER_BYTES = b"\x5a\x5a"

# header(2) + cnt(1) + len(2) ... data ... checksum(2)
PREFIX_SIZE = 5
SUFFIX_SIZE = 2
OVERHEAD = PREFIX_SIZE + SUFFIX_SIZE

# DATA length for the V3.9.8 layout below. Not enforced — see the module
# docstring — but a mismatch is worth surfacing to the operator.
EXPECTED_DATA_LEN = 650

# Floor for a non-zero joint angle in `looks_sane`. Orders of magnitude below
# any encoder resolution, and orders of magnitude above the denormals a
# byte-swapped double decodes to.
_MIN_PLAUSIBLE_ANGLE = 1e-12

# A LEN wildly larger than the documented structure means we synced on a 0x5A5A
# that was really payload bytes. Bound it so a bogus header cannot make the
# reader wait for gigabytes before it resyncs.
MAX_DATA_LEN = 4096

_KIND_FMT = {
    "u8": "B",
    "i8": "b",
    "u16": "H",
    "i32": "i",
    "f32": "f",
    "f64": "d",
}
_KIND_SIZE = {"u8": 1, "i8": 1, "u16": 2, "i32": 4, "f32": 4, "f64": 8, "str": 1, "raw": 1}

# (name, kind, count) in wire order. Offsets are derived, never hand-written —
# transcribing 76 offsets by hand is exactly the error this table exists to
# avoid. `test_frame_8083.py` pins the ones WeldFlex depends on.
_SPEC: tuple[tuple[str, str, int], ...] = (
    ("program_state", "u8", 1),           # 1=stop 2=run 3=suspended 4=drag
    ("error_code", "u8", 1),              # coarse 0-12, Appendix 1
    ("robot_mode", "u8", 1),              # 0=auto 1=manual 2=drag
    ("jt_cur_pos", "f64", 6),             # deg
    ("tl_cur_pos", "f64", 6),             # x/y/z mm, a/b/c deg
    ("tool_num", "i32", 1),
    ("jt_cur_tor", "f64", 6),             # N*m
    ("program_name", "str", 20),
    ("prog_total_line", "u8", 1),         # uint8 — caps at 255
    ("prog_cur_line", "u8", 1),           # uint8 — caps at 255
    ("cl_dgt_output_h", "u8", 1),         # control box DO 15-8
    ("cl_dgt_output_l", "u8", 1),         # control box DO 7-0
    ("tl_dgt_output_l", "u8", 1),         # tool DO, bit0-bit1 only
    ("cl_dgt_input_h", "u8", 1),          # control box DI 15-8
    ("cl_dgt_input_l", "u8", 1),          # control box DI 7-0
    ("tl_dgt_input_l", "u8", 1),          # tool DI, bit0-bit1 only
    ("ft_data", "f64", 6),                # Fx Fy Fz [N], Tx Ty Tz [N*m]
    ("ft_act_status", "u8", 1),           # 0=reset 1=activated
    ("emergency_stop", "u8", 1),          # 1=e-stop
    ("robot_motion_done", "i32", 1),      # 1=in place
    ("gripper_motion_done", "u8", 1),
    ("servo_id", "u8", 1),
    ("servo_errcode", "i32", 1),
    ("servo_state", "i32", 1),
    ("servo_actual_pos", "f64", 1),
    ("servo_actual_speed", "f32", 1),
    ("servo_actual_torque", "f32", 1),
    ("exaxis_out_slimit_error", "u8", 1),
    # 4 axes x 29 bytes (Table 2-3). WeldFlex has no external axis; kept as raw
    # bytes so the offsets past it stay correct without modelling the struct.
    ("exaxis_status", "raw", 116),
    ("exaxis_active_flag", "u8", 1),
    ("exaxis_motion_status", "u8", 1),
    ("cl_analog_input", "u16", 2),        # 0-4095
    ("tl_analog_input", "u16", 1),
    ("cl_analog_output", "u16", 2),
    ("tl_analog_output", "u16", 1),
    ("gripper_fault_id", "u8", 1),
    ("gripper_fault", "u16", 1),
    ("gripper_active", "u16", 1),
    ("gripper_position", "u8", 1),
    ("gripper_speed", "i8", 1),
    ("gripper_current", "i8", 1),
    ("gripper_temp", "i32", 1),
    ("gripper_voltage", "i32", 1),
    ("gripper_rot_num", "f32", 1),
    ("gripper_rot_speed", "u8", 1),
    ("gripper_rot_torque", "u8", 1),
    ("main_errcode", "i32", 1),           # the detailed fault pair
    ("sub_errcode", "i32", 1),
    ("weld_break_off_state", "u8", 1),    # Table 2-4
    ("weld_arc_state", "u8", 1),
    ("smart_tool_state", "i32", 1),
    ("tool_coord", "f64", 6),
    ("wobj_coord", "f64", 6),
    ("ex_tool_coord", "f64", 6),
    ("ex_axis_coord", "f64", 6),
    ("load", "f64", 1),
    ("load_cog", "f64", 3),               # x, y, z
)


# Enumerations the manual spells out. `PROGRAM_STATES` agrees exactly with
# `robot_service.STATE_MAP`, which is worth knowing: the feed and the XML-RPC
# `GetProgramState` speak the same language, so the cutover is a change of
# source, not of meaning.
PROGRAM_STATES = {1: "stopped", 2: "running", 3: "paused", 4: "drag"}
ROBOT_MODES = {0: "automatic", 1: "manual", 2: "drag"}

# Appendix 1. Coarse and separate from `main_errcode`/`sub_errcode`, which carry
# the detailed pair.
ERROR_CODES = {
    0: "no faults",
    1: "drive failure",
    2: "soft limit exceeded",
    3: "collision",
    4: "singular pose",
    5: "slave error",
    6: "command point incorrect",
    7: "IO error",
    8: "axle device error",
    9: "file error",
    10: "parameter incorrect",
    11: "extension shaft soft limit",
    12: "joint configuration warning",
}


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    kind: str
    count: int
    size: int          # total bytes for this field, all elements included

    def end(self) -> int:
        return self.offset + self.size


def _build_fields() -> tuple[Field, ...]:
    fields: list[Field] = []
    offset = 0
    for name, kind, count in _SPEC:
        size = _KIND_SIZE[kind] * count
        fields.append(Field(name=name, offset=offset, kind=kind, count=count, size=size))
        offset += size
    return tuple(fields)


FIELDS: tuple[Field, ...] = _build_fields()
FIELD_BY_NAME: dict[str, Field] = {f.name: f for f in FIELDS}
LAYOUT_SIZE = FIELDS[-1].end()

# Every signal WeldFlex reads. A frame at least this long carries all of them,
# whatever the controller does with the trailing coordinate-system block.
REQUIRED_LEN = FIELD_BY_NAME["sub_errcode"].end()


def _decode(field: Field, data: bytes, little_endian: bool) -> Any:
    if field.kind == "raw":
        return bytes(data[field.offset : field.end()])
    if field.kind == "str":
        raw = data[field.offset : field.end()]
        # Fixed-width char array, NUL padded.
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")

    order = "<" if little_endian else ">"
    fmt = f"{order}{field.count}{_KIND_FMT[field.kind]}"
    values = struct.unpack_from(fmt, data, field.offset)
    return values[0] if field.count == 1 else list(values)


def parse(data: bytes, little_endian: bool = True) -> dict[str, Any]:
    """Decode DATA into a field dict.

    Fields whose bytes are not present are omitted rather than set to None, so a
    caller can tell "this firmware did not send it" from "it was sent as zero".
    """
    available = len(data)
    out: dict[str, Any] = {}
    for field in FIELDS:
        if field.end() > available:
            break  # fields are in wire order; nothing after this fits either
        out[field.name] = _decode(field, data, little_endian)
    return out


def looks_sane(parsed: dict[str, Any]) -> bool:
    """Cheap structural check that the layout and endianness are right.

    Deliberately uses only fields the manual gives explicit ranges for, plus the
    joint angles. Do not extend this into a plausibility check on *values*; it
    exists to catch a misparse, not a robot in an unusual pose.

    The byte-order check rests entirely on the joints: `program_state`,
    `robot_mode` and `error_code` are single bytes and read identically either
    way. A human-scale float64 keeps its exponent in the high-address bytes, so
    reading it byte-swapped moves near-zero bytes into the exponent and yields a
    *denormal* — ~1e-320, not the ~1e300 you might expect. That is why the check
    below is two-sided: an upper bound alone passes byte-swapped data happily,
    which is precisely how the first version of this function let it through.
    """
    state = parsed.get("program_state")
    if not isinstance(state, int) or not 0 <= state <= 4:
        return False
    mode = parsed.get("robot_mode")
    if not isinstance(mode, int) or not 0 <= mode <= 2:
        return False
    code = parsed.get("error_code")
    if not isinstance(code, int) or not 0 <= code <= 12:
        return False

    joints = parsed.get("jt_cur_pos")
    if not isinstance(joints, list) or len(joints) != 6:
        return False
    for angle in joints:
        if not math.isfinite(angle) or abs(angle) > 720.0:
            return False
        # Exactly 0.0 is a normal reading; a denormal is not a joint angle.
        if angle != 0.0 and abs(angle) < _MIN_PLAUSIBLE_ANGLE:
            return False
    return True


def checksum(frame: bytes, data_len: int) -> int:
    """Sum of every byte from the frame header through the end of DATA."""
    return sum(frame[: PREFIX_SIZE + data_len]) & 0xFFFF


def pack(values: dict[str, Any], little_endian: bool = True) -> bytes:
    """Encode a field dict into a full-length DATA payload, zero-filled.

    The inverse of `parse`, for `tools/stub_robot.py` and the tests. It lives
    here rather than in either caller so that a change to `_SPEC` moves the
    encoder and the decoder together — a stub that packed to a stale layout
    would manufacture passing tests against a format the robot never sends.
    """
    buf = bytearray(LAYOUT_SIZE)
    order = "<" if little_endian else ">"
    for name, value in values.items():
        field = FIELD_BY_NAME[name]
        if field.kind == "raw":
            buf[field.offset : field.offset + len(value)] = value
            continue
        if field.kind == "str":
            raw = value.encode("ascii", errors="replace")[: field.size]
            buf[field.offset : field.offset + len(raw)] = raw
            continue
        items = value if isinstance(value, (list, tuple)) else [value]
        struct.pack_into(
            f"{order}{field.count}{_KIND_FMT[field.kind]}", buf, field.offset, *items
        )
    return bytes(buf)


def build_frame(data: bytes, cnt: int = 0, little_endian: bool = True) -> bytes:
    """Assemble a well-formed frame around `data`.

    Used by the tests and by `tools/stub_robot.py`; keeping it next to the
    decoder is what stops the two from drifting apart.
    """
    order = "<" if little_endian else ">"
    body = struct.pack(f"{order}HBH", HEADER, cnt & 0xFF, len(data)) + data
    return body + struct.pack(f"{order}H", sum(body) & 0xFFFF)


@dataclass
class ReaderStats:
    frames_ok: int = 0
    checksum_fail: int = 0
    resync_bytes: int = 0      # bytes discarded hunting for a header
    bad_len: int = 0           # header found but LEN implausible
    short_data: int = 0        # LEN < REQUIRED_LEN, some signals missing
    unexpected_len: int = 0    # LEN != EXPECTED_DATA_LEN

    def as_dict(self) -> dict[str, int]:
        return {
            "frames_ok": self.frames_ok,
            "checksum_fail": self.checksum_fail,
            "resync_bytes": self.resync_bytes,
            "bad_len": self.bad_len,
            "short_data": self.short_data,
            "unexpected_len": self.unexpected_len,
        }


class FrameReader:
    """Turns an arbitrarily-chunked byte stream into validated frames.

    A TCP read boundary lands wherever it lands, so this must tolerate a frame
    split across any number of chunks and several frames arriving in one. It
    also has to recover from mid-stream garbage: on a checksum failure it steps
    past the bad header rather than trusting its LEN, because a corrupt LEN
    would otherwise consume the good frame behind it.
    """

    def __init__(self, max_buffer: int = 1 << 20, little_endian: bool | None = None) -> None:
        self._buf = bytearray()
        self._max_buffer = max_buffer
        self.stats = ReaderStats()
        # None means "not yet known". LEN and CHECKSUM are uint16 and the header
        # 0x5A5A is a palindrome, so a frame gives no free hint about byte order
        # — it has to be discovered by finding which reading produces a length
        # and checksum that agree. Latched on the first frame that validates,
        # because a stream does not change byte order midway.
        self.little_endian: bool | None = little_endian

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        """Add received bytes; return every complete, valid frame they finish."""
        self._buf.extend(chunk)
        if len(self._buf) > self._max_buffer:
            # Nothing valid can be this far behind. Keep the tail so a frame in
            # flight at the moment of the overflow can still complete.
            drop = len(self._buf) - self._max_buffer
            del self._buf[:drop]
            self.stats.resync_bytes += drop
        return list(self._drain())

    def _drain(self) -> Iterator[dict[str, Any]]:
        while True:
            frame = self._next_frame()
            if frame is None:
                return
            yield frame

    def _next_frame(self) -> dict[str, Any] | None:
        while True:
            if not self._seek_header():
                return None
            if len(self._buf) < PREFIX_SIZE:
                return None

            # Once the order is known there is one candidate; before that, both
            # are tried and the one whose LEN and CHECKSUM agree wins.
            orders = (True, False) if self.little_endian is None else (self.little_endian,)
            incomplete = False
            saw_plausible_len = False

            for little in orders:
                byteorder = "little" if little else "big"
                data_len = int.from_bytes(self._buf[3:5], byteorder)
                if data_len == 0 or data_len > MAX_DATA_LEN:
                    continue
                saw_plausible_len = True

                total = data_len + OVERHEAD
                if len(self._buf) < total:
                    incomplete = True
                    continue

                frame = bytes(self._buf[:total])
                got = int.from_bytes(frame[total - SUFFIX_SIZE : total], byteorder)
                if got != checksum(frame, data_len):
                    continue

                del self._buf[:total]
                self.little_endian = little
                return self._accept(frame, data_len, little)

            if incomplete:
                return None  # wait for more bytes before judging this header

            # Nothing validated. A plausible length that failed its checksum is
            # corruption; an implausible one means this 0x5A5A was payload.
            if saw_plausible_len:
                self.stats.checksum_fail += 1
            else:
                self.stats.bad_len += 1
            self._skip_header()

    def _accept(self, frame: bytes, data_len: int, little_endian: bool) -> dict[str, Any]:
        data = frame[PREFIX_SIZE : PREFIX_SIZE + data_len]
        if data_len != EXPECTED_DATA_LEN:
            self.stats.unexpected_len += 1
        if data_len < REQUIRED_LEN:
            self.stats.short_data += 1

        parsed = parse(data, little_endian=little_endian)
        parsed["_cnt"] = frame[2]
        parsed["_data_len"] = data_len
        parsed["_little_endian"] = little_endian
        # Retained so a caller that finds the decode implausible can retry the
        # same bytes in the other byte order. Underscore-prefixed keys are
        # transport metadata and are stripped before publication.
        parsed["_raw"] = data
        self.stats.frames_ok += 1
        return parsed

    def _seek_header(self) -> bool:
        """Drop bytes until the buffer starts with the header. False if absent."""
        if self._buf[:2] == HEADER_BYTES:
            return True
        index = self._buf.find(HEADER_BYTES)
        if index < 0:
            # A header can straddle a chunk boundary, so retain one trailing byte.
            keep = 1 if self._buf[-1:] == HEADER_BYTES[:1] else 0
            drop = len(self._buf) - keep
            if drop > 0:
                del self._buf[: drop]
                self.stats.resync_bytes += drop
            return False
        del self._buf[:index]
        self.stats.resync_bytes += index
        return True

    def _skip_header(self) -> None:
        """Step past the current (untrustworthy) header so the search resumes."""
        del self._buf[:2]
        self.stats.resync_bytes += 2
