"""The port-8083 frame decoder, against synthetic bytes.

No socket and no controller: the frame layout is the part of this feature most
likely to be wrong and the part cheapest to prove offline. Every field offset
WeldFlex depends on is pinned here, so a future edit to `_SPEC` that shifts them
fails loudly instead of silently reading the wrong bytes off a live robot.

Byte order is asserted little-endian throughout. The manual never states it —
see `looks_sane` — so these tests encode the assumption the live validation
session has to confirm.
"""

import struct

import pytest

import frame_8083 as f8


def make_data(_little_endian: bool = True, **overrides) -> bytes:
    """A full-length DATA payload with sane defaults, patched per test."""
    values = {
        "program_state": 2,       # running
        "error_code": 0,
        "robot_mode": 0,
        "jt_cur_pos": [10.0, -20.0, 30.0, -40.0, 50.0, -60.0],
        "tl_cur_pos": [100.5, 200.25, 300.125, 1.0, 2.0, 3.0],
        "prog_cur_line": 42,
        "prog_total_line": 113,
        "cl_dgt_input_l": 0b0000_0011,
        "cl_dgt_output_l": 0b0101_0000,
        "ft_data": [1.5, -2.5, -88.75, 0.1, 0.2, 0.3],
        "ft_act_status": 1,
        "emergency_stop": 0,
        "main_errcode": 0,
        "sub_errcode": 0,
        "program_name": "WeldFlex",
    }
    values.update(overrides)
    return f8.pack(values, little_endian=_little_endian)


def test_pack_is_the_inverse_of_parse():
    """`pack` is what the stub controller emits; if it drifts, so do the tests."""
    parsed = f8.parse(make_data(prog_cur_line=200, cl_dgt_output_l=0xA5))
    assert parsed["prog_cur_line"] == 200
    assert parsed["cl_dgt_output_l"] == 0xA5
    assert len(make_data()) == f8.LAYOUT_SIZE


def test_pack_honours_byte_order():
    data = make_data(_little_endian=False)
    assert f8.looks_sane(f8.parse(data, little_endian=False)) is True
    assert f8.looks_sane(f8.parse(data, little_endian=True)) is False


# --- layout ---------------------------------------------------------------


def test_layout_total_is_650_bytes():
    """Table 2-2 summed by hand. A change here means the spec table moved."""
    assert f8.LAYOUT_SIZE == 650
    assert f8.EXPECTED_DATA_LEN == 650


@pytest.mark.parametrize(
    "name, offset",
    [
        ("program_state", 0),
        ("error_code", 1),
        ("robot_mode", 2),
        ("jt_cur_pos", 3),
        ("tl_cur_pos", 51),
        ("prog_total_line", 171),
        ("prog_cur_line", 172),
        ("cl_dgt_output_h", 173),
        ("cl_dgt_output_l", 174),
        ("cl_dgt_input_h", 176),
        ("cl_dgt_input_l", 177),
        ("ft_data", 179),
        ("ft_act_status", 227),
        ("emergency_stop", 228),
        ("main_errcode", 412),
        ("sub_errcode", 416),
    ],
)
def test_field_offsets_are_pinned(name, offset):
    assert f8.FIELD_BY_NAME[name].offset == offset


def test_tcp_z_and_fz_land_where_the_plan_says():
    """The two scalars read hottest, addressed by element rather than by field."""
    assert f8.FIELD_BY_NAME["tl_cur_pos"].offset + 2 * 8 == 67
    assert f8.FIELD_BY_NAME["ft_data"].offset + 2 * 8 == 195


def test_required_len_covers_every_signal_weldflex_reads():
    # The trailing coordinate-system block is dispensable; the fault pair is not.
    assert f8.REQUIRED_LEN == 420
    assert f8.REQUIRED_LEN < f8.LAYOUT_SIZE


# --- parse ----------------------------------------------------------------


def test_parse_round_trips_every_field_we_care_about():
    parsed = f8.parse(make_data())

    assert parsed["program_state"] == 2
    assert parsed["robot_mode"] == 0
    assert parsed["prog_cur_line"] == 42
    assert parsed["program_name"] == "WeldFlex"
    assert parsed["jt_cur_pos"] == [10.0, -20.0, 30.0, -40.0, 50.0, -60.0]
    assert parsed["tl_cur_pos"][2] == 300.125          # tcp_z
    assert parsed["ft_data"][2] == -88.75              # fz, native sign
    assert parsed["cl_dgt_input_l"] == 0b11
    assert parsed["ft_act_status"] == 1


def test_program_name_stops_at_the_nul_pad():
    parsed = f8.parse(make_data(program_name="ab"))
    assert parsed["program_name"] == "ab"


def test_short_data_yields_the_prefix_and_omits_the_rest():
    """A firmware that sends less must still give us what it did send."""
    parsed = f8.parse(make_data()[:200])

    assert parsed["prog_cur_line"] == 42          # offset 172, present
    assert parsed["cl_dgt_input_l"] == 0b11       # offset 177, present
    assert "ft_data" not in parsed                # offset 179 + 48 > 200
    assert "main_errcode" not in parsed
    # Absent, not None — the caller can tell "not sent" from "sent as zero".
    assert parsed.get("main_errcode") is None


def test_long_data_parses_the_known_prefix_and_ignores_the_tail():
    """A firmware that appends fields must not cost us the ones we know."""
    parsed = f8.parse(make_data() + b"\xab" * 64)
    assert parsed["sub_errcode"] == 0
    assert parsed["load_cog"] == [0.0, 0.0, 0.0]


def test_truncated_mid_field_does_not_partially_decode_it():
    data = make_data()[: f8.FIELD_BY_NAME["ft_data"].offset + 8]
    parsed = f8.parse(data)
    assert "ft_data" not in parsed


# --- looks_sane -----------------------------------------------------------


def test_looks_sane_accepts_a_good_frame():
    assert f8.looks_sane(f8.parse(make_data())) is True


def test_looks_sane_rejects_a_big_endian_misparse():
    """The check that earns its keep: the manual never states byte order."""
    data = make_data()
    assert f8.looks_sane(f8.parse(data, little_endian=True)) is True
    assert f8.looks_sane(f8.parse(data, little_endian=False)) is False


@pytest.mark.parametrize(
    "override",
    [
        {"program_state": 7},                                  # doc says 1-4
        {"robot_mode": 5},                                     # doc says 0-2
        {"error_code": 99},                                    # doc says 0-12
        {"jt_cur_pos": [1e300, 0.0, 0.0, 0.0, 0.0, 0.0]},      # not a degree
    ],
)
def test_looks_sane_rejects_out_of_range_fields(override):
    assert f8.looks_sane(f8.parse(make_data(**override))) is False


def test_looks_sane_rejects_a_frame_too_short_to_judge():
    assert f8.looks_sane(f8.parse(make_data()[:10])) is False


def test_looks_sane_rejects_nan_joints():
    assert f8.looks_sane(f8.parse(make_data(jt_cur_pos=[float("nan")] + [0.0] * 5))) is False


def test_looks_sane_rejects_denormal_joints():
    """The specific shape a byte-swapped float64 takes. Guards the check above."""
    denormal = struct.unpack("<d", struct.pack(">d", 10.0))[0]
    assert 0.0 < denormal < 1e-300      # what a wrong-endian 10.0 decodes to
    assert f8.looks_sane(f8.parse(make_data(jt_cur_pos=[denormal] + [0.0] * 5))) is False


def test_looks_sane_accepts_all_joints_at_exactly_zero():
    """A robot at its zero pose is normal; only *denormals* are the tell."""
    assert f8.looks_sane(f8.parse(make_data(jt_cur_pos=[0.0] * 6))) is True


# --- framing --------------------------------------------------------------


def test_build_frame_shape_and_checksum():
    frame = f8.build_frame(b"\x01\x02\x03", cnt=7)

    assert frame[:2] == b"\x5a\x5a"
    assert frame[2] == 7
    assert int.from_bytes(frame[3:5], "little") == 3
    assert len(frame) == 3 + f8.OVERHEAD
    assert int.from_bytes(frame[-2:], "little") == sum(frame[:-2]) & 0xFFFF


def test_reader_accepts_one_whole_frame():
    reader = f8.FrameReader()
    frames = reader.feed(f8.build_frame(make_data(), cnt=3))

    assert len(frames) == 1
    assert frames[0]["_cnt"] == 3
    assert frames[0]["_data_len"] == 650
    assert frames[0]["prog_cur_line"] == 42
    assert reader.stats.frames_ok == 1
    assert reader.stats.checksum_fail == 0
    assert reader.stats.resync_bytes == 0


def test_reader_accepts_several_frames_in_one_chunk():
    reader = f8.FrameReader()
    blob = b"".join(f8.build_frame(make_data(prog_cur_line=n), cnt=n) for n in range(4))

    frames = reader.feed(blob)

    assert [fr["prog_cur_line"] for fr in frames] == [0, 1, 2, 3]
    assert reader.stats.frames_ok == 4


def test_reader_reassembles_a_frame_split_across_reads():
    """A TCP read boundary lands wherever it lands."""
    reader = f8.FrameReader()
    frame = f8.build_frame(make_data(), cnt=9)

    for index in range(len(frame) - 1):
        assert reader.feed(frame[index : index + 1]) == []
    frames = reader.feed(frame[-1:])

    assert len(frames) == 1
    assert frames[0]["_cnt"] == 9


def test_reader_reassembles_across_awkward_chunk_sizes():
    reader = f8.FrameReader()
    blob = b"".join(f8.build_frame(make_data(), cnt=n) for n in range(3))

    seen = []
    for index in range(0, len(blob), 97):
        seen.extend(reader.feed(blob[index : index + 97]))

    assert len(seen) == 3
    assert reader.stats.checksum_fail == 0


def test_reader_skips_leading_garbage_and_counts_it():
    reader = f8.FrameReader()
    frames = reader.feed(b"\x00\xff\x13" + f8.build_frame(make_data()))

    assert len(frames) == 1
    assert reader.stats.resync_bytes == 3


def test_reader_recovers_the_frame_behind_a_bad_checksum():
    """A corrupt LEN must not be trusted to say where the next frame starts."""
    reader = f8.FrameReader()
    bad = bytearray(f8.build_frame(make_data(prog_cur_line=1)))
    bad[-1] ^= 0xFF
    good = f8.build_frame(make_data(prog_cur_line=2))

    frames = reader.feed(bytes(bad) + good)

    assert [fr["prog_cur_line"] for fr in frames] == [2]
    assert reader.stats.checksum_fail == 1
    assert reader.stats.frames_ok == 1


def test_reader_recovers_from_a_false_header_inside_payload():
    """No frame is lost — the false header is skipped, not the frame behind it.

    Before the byte order is latched, both readings of this header's LEN are
    plausible (4 little-endian, 1024 big-endian), so the decoder has to wait for
    enough bytes to disprove the wider one. Once it can, it discards only the
    two header bytes and re-finds the real frame intact. A stream that stops
    dead mid-doubt would stall until it resumed, which is why latching matters.
    """
    reader = f8.FrameReader()
    stream = b"\x5a\x5a\x00\x04\x00" + b"".join(
        f8.build_frame(make_data(prog_cur_line=n), cnt=n) for n in range(3)
    )

    frames = reader.feed(stream)

    assert [fr["prog_cur_line"] for fr in frames] == [0, 1, 2]
    assert reader.little_endian is True


def test_reader_recovers_immediately_once_the_byte_order_is_latched():
    reader = f8.FrameReader()
    assert len(reader.feed(f8.build_frame(make_data(prog_cur_line=1)))) == 1
    assert reader.little_endian is True

    frames = reader.feed(b"\x5a\x5a\x00\x04\x00" + f8.build_frame(make_data(prog_cur_line=2)))

    assert [fr["prog_cur_line"] for fr in frames] == [2]


def test_reader_rejects_a_len_implausible_in_either_byte_order():
    reader = f8.FrameReader()
    bogus = b"\x5a\x5a\x00\xff\xff"      # 65535 read either way — never valid

    frames = reader.feed(bogus + f8.build_frame(make_data()))

    assert len(frames) == 1
    assert reader.stats.bad_len == 1


def test_reader_latches_big_endian_framing():
    """LEN and CHECKSUM are uint16 and 0x5A5A is a palindrome, so the order has
    to be discovered by finding which reading makes them agree."""
    reader = f8.FrameReader()
    frames = reader.feed(f8.build_frame(make_data(_little_endian=False), little_endian=False))

    assert len(frames) == 1
    assert reader.little_endian is False
    assert frames[0]["_little_endian"] is False
    assert frames[0]["prog_cur_line"] == 42
    assert f8.looks_sane(frames[0]) is True


def test_reader_honours_an_explicitly_pinned_byte_order():
    reader = f8.FrameReader(little_endian=True)
    assert reader.feed(f8.build_frame(make_data(_little_endian=False), little_endian=False)) == []
    assert reader.little_endian is True


def test_reader_holds_a_split_header_across_the_boundary():
    reader = f8.FrameReader()
    frame = f8.build_frame(make_data())

    assert reader.feed(b"\x00\x5a") == []      # trailing 0x5a may start a header
    frames = reader.feed(b"\x5a" + frame[2:])

    assert len(frames) == 1


def test_reader_flags_a_short_but_valid_frame():
    reader = f8.FrameReader()
    frames = reader.feed(f8.build_frame(make_data()[:300]))

    assert len(frames) == 1
    assert reader.stats.short_data == 1
    assert reader.stats.unexpected_len == 1
    # 300 bytes still covers force (ends at 227) but not the fault pair (at 412),
    # which is exactly why REQUIRED_LEN is the gate rather than "any frame".
    assert frames[0]["ft_data"][2] == -88.75
    assert "main_errcode" not in frames[0]


def test_reader_flags_an_unexpected_length_that_is_still_usable():
    reader = f8.FrameReader()
    frames = reader.feed(f8.build_frame(make_data() + b"\x00" * 8))

    assert len(frames) == 1
    assert reader.stats.unexpected_len == 1
    assert reader.stats.short_data == 0
    assert frames[0]["sub_errcode"] == 0


def test_reader_survives_a_stream_of_pure_noise():
    reader = f8.FrameReader()
    assert reader.feed(bytes(range(256)) * 8) == []
    # And still finds the next real frame afterwards.
    assert len(reader.feed(f8.build_frame(make_data()))) == 1


def test_reader_buffer_is_bounded():
    reader = f8.FrameReader(max_buffer=4096)
    reader.feed(b"\x00" * 100_000)
    assert len(reader._buf) <= 4096
    assert len(reader.feed(f8.build_frame(make_data()))) == 1


def test_stats_are_serialisable_for_the_diagnostics_panel():
    reader = f8.FrameReader()
    reader.feed(f8.build_frame(make_data()))
    assert reader.stats.as_dict()["frames_ok"] == 1
