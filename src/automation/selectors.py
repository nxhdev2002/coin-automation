SELECTORS = {
    "login_url": "https://www.tiktok.com/login",
    "recharge_url": "https://www.tiktok.com/coin",

    "qr_channel_item": '[data-e2e="channel-item"]',
    "qr_code_container": '[data-e2e="qr-code"]',
    "qr_page_title": '[data-e2e="qr-page-title"]',
    "qr_back_btn": '[data-e2e="back-btn"]',

    "login_button": '[data-e2e="top-login-button"]',
    "profile_icon": '[data-e2e="profile-icon"]',
    "wallet_user_name": '[data-e2e="wallet-user-name"]',
    "wallet_coins_balance": '[data-e2e="wallet-coins-balance"]',

    "wallet_title_get_coins": '[data-e2e="wallet-title-get-coins"]',
    "wallet_coins_packages": '[data-e2e="wallet-coins-packages"]',
    "wallet_package_selected": '[data-e2e="wallet-package-selected"]',
    "wallet_package": '[data-e2e="wallet-package-{index}"]',
    "wallet_package_custom": '[data-e2e="wallet-package-custom"]',
    "wallet_package_coin_num": '[data-e2e="wallet-package-coin-num-{index}"]',
    "wallet_package_price": '[data-e2e="wallet-package-price-{index}"]',
    "wallet_total_price": '[data-e2e="wallet-total-price"]',
    "wallet_buy_now_button": '[data-e2e="wallet-buy-now-button"]',

    "cashier_header": '[data-e2e="cashier-header"]',
    "cashier_header_close": '[data-e2e="cashier-header-close"]',
    "cashier_user_name": '[data-e2e="cashier-user-name"]',
    "cashier_order_coin_num": '[data-e2e="cashier-order-coin-num"]',
    "cashier_order_coin_total_price": '[data-e2e="cashier-order-coin-total-price"]',
    "cashier_footer_button": '[data-e2e="cashier-footer-button"]',
    "payment_method_list": '[data-e2e="payment-method-list"]',
    "payment_method_item_ccdc": '[data-e2e="payment-method-item-ccdc"]',
    "payment_method_item_ccdc_stored": '[data-e2e="payment-method-item-ccdc-stored"]',
    "payment_method_save_button": '[data-e2e="payment-method-save-button"]',

    "pipopay_iframe": 'iframe[src*="pipopay"]',
    "card_number_input": 'input[placeholder*="card number" i]',
    "card_cvv_input": 'input[placeholder*="CVV" i]',
    "card_name_input": 'input[placeholder*="Cardholder" i]',
    "card_expiry_input": 'input[placeholder*="MM" i]',

    "end_result_url_pattern": "/coin/end-result",
    "payment_success_text": "Purchase completed",
    "payment_failed_text": "Card not authenticated",

    "captcha_keywords": ["captcha", "verify-image", "slider", "geetest", "secsdk", "tcaptcha"],
    "otp_keywords": ["otp", "verification code", "one-time", "security code", "3ds", "3d secure", "authenticate"],
}
