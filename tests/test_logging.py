from app.utils.logging import mask_secrets, mask_telegram_token


def test_mask_telegram_token_in_url() -> None:
    url = "https://api.telegram.org/bot123456:ABCdef_789/sendMessage"
    assert mask_telegram_token(url) == "https://api.telegram.org/bot123456:***MASKED***/sendMessage"


def test_mask_secrets_masks_openai_key() -> None:
    fake_key = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz"
    text = f"Authorization: Bearer {fake_key}"
    assert mask_secrets(text) == "Authorization: Bearer sk-proj-***MASKED***"
