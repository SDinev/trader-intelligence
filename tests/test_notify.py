from notify import post_discord_message


def test_post_discord_message_sends_content_to_webhook_url(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("notify.requests.post", fake_post)

    post_discord_message("https://discord.example/webhook", "hello brief")

    assert captured["url"] == "https://discord.example/webhook"
    assert captured["json"] == {"content": "hello brief"}
