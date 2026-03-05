from app.handlers.alerts import _parse_confirm_payload


def test_parse_confirm_payload_valid_value() -> None:
    assert _parse_confirm_payload("alrt_ok:CPU:91") == ("CPU", 91.0)


def test_parse_confirm_payload_rejects_invalid_metric() -> None:
    assert _parse_confirm_payload("alrt_ok:GPU:75") is None


def test_parse_confirm_payload_rejects_invalid_numbers() -> None:
    assert _parse_confirm_payload("alrt_ok:RAM:not-a-number") is None
    assert _parse_confirm_payload("alrt_ok:RAM:101") is None
