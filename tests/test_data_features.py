from backend.data import add_chat_message, add_pending_activation


def test_chat_and_pending_activation_are_stored():
    data = {}

    message = add_chat_message(data, "user", "device-123", "Xin chào admin")
    pending = add_pending_activation(data, "device-123", "10.10.10.20", "ABCD-EFGH-IJKL")

    assert message["sender"] == "user"
    assert message["text"] == "Xin chào admin"
    assert data["chat_messages"][0]["device_id"] == "device-123"
    assert pending["voucher_code"] == "ABCD-EFGH-IJKL"
    assert data["pending_activations"]["device-123"]["voucher_code"] == "ABCD-EFGH-IJKL"
