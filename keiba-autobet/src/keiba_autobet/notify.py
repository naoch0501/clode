"""Discord / Slack 互換 Webhook への通知（任意）。"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, webhook_url: str = ""):
        self.url = webhook_url

    def send(self, text: str) -> None:
        if not self.url:
            return
        try:
            # Discord は content、Slack は text を読む
            requests.post(self.url, json={"content": text[:1900], "text": text[:1900]}, timeout=10)
        except Exception as ex:
            log.warning("通知に失敗: %s", ex)
