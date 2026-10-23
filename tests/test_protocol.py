"""A04：帧编解码的七类输入 + 边界与异常。

七类必测输入（对应 docs/protocol.md 附录 A 与《实施步骤清单》S08）：
    ① ASCII  ② 中文  ③ emoji  ④ 空体  ⑤ 超长体  ⑥ 坏 magic  ⑦ 半包

另外把「协议文档第 5 章的黄金字节」固定成回归用例：文档与代码只要有一边
改了字段或分隔符，这条就会红。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from shared.errors import ErrorCode
from shared.protocol import (
    HEADER,
    MAGIC,
    MAX_BODY,
    MAX_FRAME,
    MAX_TEXT_BYTES,
    VERSION,
    MsgType,
    ProtocolError,
    encode_frame,
    is_known_type,
    read_frame,
    text_too_long,
)

# ---------------------------------------------------------------- 工具


def raw_frame(
    msg_type: int,
    payload: bytes = b"{}",
    *,
    magic: int = MAGIC,
    version: int = VERSION,
    length: int | None = None,
) -> bytes:
    """手工拼一帧，用来构造那些 encode_frame 不肯生成的坏帧。"""
    return HEADER.pack(magic, version, msg_type, len(payload) if length is None else length) + payload


def reader_with(*chunks: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return reader


def encode_body_json(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------- ① ASCII


async def test_ascii_roundtrip() -> None:
    body = {"msg_id": "alice-000001", "text": "hello world"}
    frame = encode_frame(MsgType.CHAT_GROUP, body)

    msg_type, decoded = await read_frame(reader_with(frame))

    assert msg_type == MsgType.CHAT_GROUP
    assert decoded == body


# ---------------------------------------------------------------- ② 中文


async def test_chinese_roundtrip_keeps_utf8_bytes() -> None:
    text = "你好，世界"
    frame = encode_frame(MsgType.CHAT_GROUP, {"text": text})

    # 一个汉字 3 字节，绝不能被转义成 \uXXXX（那样会变成 6 字节，长度全错）
    assert "你".encode("utf-8") in frame
    assert b"\\u" not in frame
    assert len(text.encode("utf-8")) == 15

    _, decoded = await read_frame(reader_with(frame))
    assert decoded["text"] == text


# ---------------------------------------------------------------- ③ emoji


async def test_emoji_roundtrip_counts_four_bytes() -> None:
    text = "你好 😀 世界"
    frame = encode_frame(MsgType.CHAT_GROUP, {"text": text})
    _, decoded = await read_frame(reader_with(frame))

    # 汉字 3 字节、emoji 4 字节：len(str)=7，但编码后是 18 字节
    assert len(text) == 7
    assert len(text.encode("utf-8")) == 18
    assert decoded["text"] == text


# ---------------------------------------------------------------- ④ 空体


async def test_empty_body_encodes_as_empty_object() -> None:
    frame = encode_frame(MsgType.PING)
    assert frame == bytes.fromhex("43480130000000027B7D")
    assert frame[HEADER.size:] == b"{}"

    msg_type, body = await read_frame(reader_with(frame))
    assert msg_type == MsgType.PING
    assert body == {}


async def test_zero_length_body_is_rejected() -> None:
    # 空体必须写成 {}（2 字节）；length=0 意味着没有 JSON，按 2003 处理
    frame = raw_frame(MsgType.PING, payload=b"", length=0)
    with pytest.raises(ProtocolError) as exc:
        await read_frame(reader_with(frame))
    assert exc.value.code == ErrorCode.BAD_FRAME


# ---------------------------------------------------------------- ⑤ 超长体


def test_body_at_limit_is_accepted() -> None:
    # {"t":"aaaa…"} 恰好 8 字节开销，所以 n = MAX_BODY - 8 时整帧正好 65536
    body = {"t": "a" * (MAX_BODY - 8)}
    frame = encode_frame(MsgType.CHAT_GROUP, body)
    assert len(frame) == MAX_FRAME


def test_oversized_body_is_rejected_on_encode() -> None:
    with pytest.raises(ValueError, match="超过上限"):
        encode_frame(MsgType.CHAT_GROUP, {"t": "a" * (MAX_BODY - 7)})


async def test_oversized_length_field_is_rejected_without_reading() -> None:
    # 只发 8 字节头，声称 length = MAX_BODY + 1：必须在读体之前就拒绝
    frame = HEADER.pack(MAGIC, VERSION, MsgType.CHAT_GROUP, MAX_BODY + 1)
    with pytest.raises(ProtocolError) as exc:
        await read_frame(reader_with(frame))
    assert exc.value.reason == "frame_too_large"
    assert exc.value.code is None  # 不回包，直接断


def test_body_must_be_dict() -> None:
    with pytest.raises(ValueError, match="必须是 dict"):
        encode_frame(MsgType.CHAT_GROUP, ["not", "a", "dict"])  # type: ignore[arg-type]


# ---------------------------------------------------------------- ⑥ 坏帧头


async def test_bad_magic_is_rejected() -> None:
    frame = raw_frame(MsgType.PING, magic=0x1234)
    with pytest.raises(ProtocolError) as exc:
        await read_frame(reader_with(frame))
    assert exc.value.reason == "bad_magic"
    assert exc.value.code is None


async def test_bad_version_maps_to_1003() -> None:
    frame = raw_frame(MsgType.PING, version=2)
    with pytest.raises(ProtocolError) as exc:
        await read_frame(reader_with(frame))
    assert exc.value.code == ErrorCode.VERSION_MISMATCH


async def test_bad_json_maps_to_2003() -> None:
    frame = raw_frame(MsgType.CHAT_GROUP, payload=b"{oops")
    with pytest.raises(ProtocolError) as exc:
        await read_frame(reader_with(frame))
    assert exc.value.code == ErrorCode.BAD_FRAME


async def test_json_array_is_not_a_valid_body() -> None:
    frame = raw_frame(MsgType.CHAT_GROUP, payload=b"[]")
    with pytest.raises(ProtocolError) as exc:
        await read_frame(reader_with(frame))
    assert exc.value.reason == "bad_body"
    assert exc.value.code == ErrorCode.BAD_FRAME


async def test_unknown_type_passes_through_raw() -> None:
    frame = raw_frame(0x7F, payload=b"{}")
    msg_type, body = await read_frame(reader_with(frame))
    assert msg_type == 0x7F
    assert body == {}
    assert is_known_type(0x7F) is False
    assert is_known_type(MsgType.PING) is True


# ---------------------------------------------------------------- ⑦ 半包


async def test_half_packet_waits_for_the_rest() -> None:
    frame = encode_frame(MsgType.CHAT_GROUP, {"msg_id": "alice-000001", "text": "你好"})
    reader = asyncio.StreamReader()
    task = asyncio.create_task(read_frame(reader))

    reader.feed_data(frame[:5])          # 首部都没读全
    await asyncio.sleep(0)
    assert not task.done()

    reader.feed_data(frame[5:-3])        # 体也不全
    await asyncio.sleep(0)
    assert not task.done()

    reader.feed_data(frame[-3:])
    reader.feed_eof()
    msg_type, body = await asyncio.wait_for(task, timeout=2)

    assert msg_type == MsgType.CHAT_GROUP
    assert body == {"msg_id": "alice-000001", "text": "你好"}


async def test_two_frames_in_one_buffer() -> None:
    # 粘包：一次喂两帧，必须按顺序拆出两条
    first = encode_frame(MsgType.PING)
    second = encode_frame(MsgType.USER_JOIN, {"user_id": 1, "username": "alice", "since": 0})
    reader = reader_with(first + second)

    t1, b1 = await read_frame(reader)
    t2, b2 = await read_frame(reader)

    assert (t1, b1) == (MsgType.PING, {})
    assert t2 == MsgType.USER_JOIN and b2["username"] == "alice"


# ---------------------------------------------------------------- 断流


async def test_eof_raises_incomplete_read() -> None:
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader_with(b""))


async def test_truncated_body_raises_incomplete_read() -> None:
    frame = raw_frame(MsgType.CHAT_GROUP, payload=b'{"te', length=10)
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader_with(frame))


# ---------------------------------------------------------------- 黄金帧


async def test_golden_login_frame_matches_protocol_doc() -> None:
    """附录 A 的标准答案：140 字节，逐字节对齐。"""
    body = {
        "username": "alice",
        "pwd_hash": "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92",
        "client_ver": "1.0",
        "resume_seq": 0,
    }
    expected = bytes.fromhex(
        "43480101000000847B22757365726E616D65223A22616C696365222C22707764"
        "5F68617368223A22386439363965656636656361643363323961336136323932"
        "3830653638366366306333663564356138366166663363613132303230633932"
        "3361646336633932222C22636C69656E745F766572223A22312E30222C227265"
        "73756D655F736571223A307D"
    )
    frame = encode_frame(MsgType.LOGIN_REQ, body)

    assert len(frame) == 140
    assert frame == expected
    assert frame[:8].hex(" ").upper() == "43 48 01 01 00 00 00 84"

    msg_type, decoded = await read_frame(reader_with(frame))
    assert msg_type == MsgType.LOGIN_REQ
    assert decoded == body


# ---------------------------------------------------------------- 正文长度


def test_text_limit_is_measured_in_bytes() -> None:
    assert text_too_long("a" * MAX_TEXT_BYTES) is False
    assert text_too_long("a" * (MAX_TEXT_BYTES + 1)) is True
    assert text_too_long("中" * 2730) is False        # 8190 字节
    assert text_too_long("中" * 2731) is True         # 8193 字节


def test_header_layout_is_eight_bytes_big_endian() -> None:
    assert HEADER.size == 8
    assert HEADER.pack(0x4348, 1, 0x10, 44) == bytes.fromhex("434801100000002C")
