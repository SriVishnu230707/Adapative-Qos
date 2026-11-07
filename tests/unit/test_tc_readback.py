import struct

import pytest

from adaptive_qos.tc_readback import attributes, decode_class


def attr(kind, payload):
    data = struct.pack("=HH", len(payload)+4, kind) + payload
    return data + b"\0" * (-len(data) % 4)


def message(rate, ceil, *, use64=False):
    params = bytearray(44)
    struct.pack_into("=I", params, 8, min(rate, 2**32-1))
    struct.pack_into("=I", params, 20, min(ceil, 2**32-1))
    options = attr(1, params)
    if use64:
        options += attr(6, struct.pack("=Q", rate)) + attr(7, struct.pack("=Q", ceil))
    return struct.pack("=B3xiIII", 0, 2, 0x7a100001, 0, 0) + attr(1, b"htb\0") + attr(2, options)


@pytest.mark.parametrize("rate,use64", [(12_500_000, False), (2**40+1, True)])
def test_exact_uapi_rates_and_64_bit_override(rate, use64):
    result = decode_class(message(rate, rate, use64=use64), 2, 0x7a100001)
    assert result["rate_bytes_per_second"] == rate
    assert result["ceil_bytes_per_second"] == rate


def test_dump_filters_other_interfaces_and_classes():
    assert decode_class(message(100, 100), 3, 0x7a100001) is None
    assert decode_class(message(100, 100), 2, 0x7a200001) is None


@pytest.mark.parametrize("data", [b"x", struct.pack("=HH", 3, 1), struct.pack("=HH", 12, 1)+b"x"])
def test_truncated_uapi_data_is_rejected(data):
    with pytest.raises(ValueError):
        if len(data) < 4:
            decode_class(data, 2, 1)
        else:
            attributes(data)
