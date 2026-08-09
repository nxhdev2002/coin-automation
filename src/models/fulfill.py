from pydantic import BaseModel


class FulfillRequest(BaseModel):
    order_id: str
    tiktok_username: str
    coin_amount: int
    card_id: str = ""
    card_number: str = ""
    card_cvv: str = ""
    card_expiry: str = ""
    card_holder_name: str = ""


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
