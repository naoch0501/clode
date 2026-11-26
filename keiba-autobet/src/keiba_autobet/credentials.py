"""楽天の認証情報を OS の資格情報ストア（Windows 資格情報マネージャー）に保存・取得する。"""

from __future__ import annotations

import getpass

import keyring

SERVICE = "keiba-autobet:rakuten"
KEYS = {"user_id": "楽天会員ID", "password": "楽天会員パスワード", "pin": "楽天競馬の暗証番号（投票時）"}


def save_interactive() -> None:
    for key, label in KEYS.items():
        prompt = f"{label}: "
        value = input(prompt) if key == "user_id" else getpass.getpass(prompt)
        if value:
            keyring.set_password(SERVICE, key, value)
    print("Windows 資格情報マネージャーに保存しました（ファイルには保存していません）。")


def load() -> dict[str, str]:
    creds = {k: keyring.get_password(SERVICE, k) or "" for k in KEYS}
    missing = [KEYS[k] for k, v in creds.items() if not v and k != "pin"]
    if missing:
        raise RuntimeError(f"認証情報が未設定です: {', '.join(missing)}。`keiba-autobet set-credentials` を実行してください")
    return creds
