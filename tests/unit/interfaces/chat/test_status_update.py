from sentinel.interfaces.chat.status_update import StreamlitStatusUpdateClient


class TestStreamlitStatusUpdateClient:
    async def test_update_status_calls_callback(self):
        messages: list[str] = []

        client = StreamlitStatusUpdateClient(on_status=messages.append)
        await client.update_status("Classifying alert...")

        assert messages == ["Classifying alert..."]

    async def test_multiple_updates(self):
        messages: list[str] = []

        client = StreamlitStatusUpdateClient(on_status=messages.append)
        await client.update_status("Step 1...")
        await client.update_status("Step 2...")
        await client.update_status("Step 3...")

        assert messages == ["Step 1...", "Step 2...", "Step 3..."]
