from pydantic import BaseModel


class FulfillRequest(BaseModel):
    # In LoginOnly mode this is a TikTokAccountLinkRequest id, not a CoinOrder id —
    # kept as `order_id` (rather than a generic `correlation_id`) to match the
    # backend's CoinFulfillmentRequest field name 1:1.
    order_id: str
    user_id: str = ""
    user_name: str = ""
    tiktok_username: str = ""
    mode: str = "TopUp"  # "TopUp" | "LoginOnly"
    profile_path: str = ""  # existing profile's browser-profile dir; empty when adding a brand-new account
    tiktok_profile_id: str = ""  # existing profile's id; empty when adding a brand-new account
    coin_amount: int = 0
    card_id: str = ""
    card_number: str = ""
    card_cvv: str = ""
    card_expiry: str = ""
    card_holder_name: str = ""
    payment_confirm_timeout_minutes: int = 5


class FulfillResult(BaseModel):
    success: bool
    failure_category: str = ""
    failure_reason: str = ""
    screenshot_path: str = ""
    captcha_encountered: bool = False
    captcha_solved: bool = False
    captcha_cost_usd: float = 0.0
    qr_code_base64: str = ""
    fulfillment_phase: str = ""
    tiktok_profile_id: str = ""
