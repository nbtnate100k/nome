"""Shared settings and data paths (project root)."""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def data_path(name: str) -> str:
    return os.path.join(ROOT, name)


BOT_TOKEN = os.getenv("BOT_TOKEN", "8513937678:AAFIxaM7U_XHuPibQM7OOMO_vJyyUFJrScs")
OWNER_ID = int(os.getenv("OWNER_ID", "7173346586"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@pluxo_official")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "pluxo_secret_2024")

ADMINS_FILE = data_path("admins.json")
SELLERS_FILE = data_path("sellers.json")
STOCK_FILE = data_path("shop_stock.json")
KEYS_FILE = data_path("keys.json")
REDEEMED_FILE = data_path("redeemed.json")
USERS_FILE = data_path("admin_users.json")
PURCHASES_FILE = data_path("purchases.json")
LOGS_FILE = data_path("action_logs.json")
STATE_FILE = data_path("bot_state.json")
GAMES_FILE = data_path("games_state.json")
SITE_USERS_FILE = data_path("site_users.json")
SITE_ORDERS_FILE = data_path("site_orders.json")
SITE_BALANCE_REQUESTS_FILE = data_path("site_balance_requests.json")

MAX_LOG_ENTRIES = 200
