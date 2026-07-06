import requests

REQUEST_TIMEOUT_SECONDS = 15


def post_discord_message(webhook_url: str, content: str) -> None:
    response = requests.post(webhook_url, json={"content": content}, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
