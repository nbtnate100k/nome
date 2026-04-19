"""
HTTP API for pluxo.net (index.html): balances, shop products, purchase notify, dice/blackjack.
Run alongside the Telegram bot from main.py (same process, shared JSON files).
"""
from __future__ import annotations

import html as html_module
import json
import logging
import os
import random
import string
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

from config import (
    BOT_TOKEN,
    ADMINS_FILE,
    GAMES_FILE,
    KEYS_FILE,
    LOGS_FILE,
    PURCHASES_FILE,
    REDEEMED_FILE,
    ROOT,
    SITE_BALANCE_REQUESTS_FILE,
    SITE_ORDERS_FILE,
    SITE_USERS_FILE,
    STOCK_FILE,
    USERS_FILE,
    WEBHOOK_SECRET,
    MAX_LOG_ENTRIES,
)

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=os.path.join(ROOT, "static"))
CORS(
    app,
    resources={r"/api/*": {"origins": "*", "allow_headers": ["Content-Type", "X-Webhook-Secret"]}},
)


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def require_secret() -> Optional[Tuple[Any, int]]:
    if request.headers.get("X-Webhook-Secret") != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    return None


def norm_user(name: str) -> str:
    return (name or "").strip().lstrip("@").lower()


def append_log(line: str) -> None:
    logs: List[str] = load_json(LOGS_FILE, [])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logs.append(f"[{ts}] {line}")
    while len(logs) > MAX_LOG_ENTRIES:
        logs.pop(0)
    save_json(LOGS_FILE, logs)


def telegram_broadcast_html(html_message: str) -> None:
    admins = load_json(ADMINS_FILE, [])
    for chat_id in admins:
        try:
            body = json.dumps(
                {"chat_id": chat_id, "text": html_message, "parse_mode": "HTML"}
            ).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=15)
        except (urllib.error.URLError, OSError) as e:
            logger.warning("Telegram notify failed for %s: %s", chat_id, e)


def ensure_user(users: Dict[str, Any], key: str) -> Dict[str, Any]:
    if key not in users:
        users[key] = {
            "balance": 0.0,
            "totalRecharge": 0.0,
            "registered_at": datetime.now(timezone.utc).strftime("%m/%d/%y"),
        }
        save_json(USERS_FILE, users)
    if "totalRecharge" not in users[key]:
        users[key]["totalRecharge"] = 0.0
    return users[key]


def brand_from_bin(bin_str: str) -> str:
    b = str(bin_str or "")
    if b.startswith("4"):
        return "VISA"
    if b.startswith("5"):
        return "MASTERCARD"
    if b.startswith("34") or b.startswith("37"):
        return "AMEX"
    return "VISA"


def _append_product_row(
    out: List[Dict[str, Any]],
    seen_keys: set,
    keys_index: Dict[str, Any],
    pid: int,
    card: Dict[str, Any],
    key_u: str,
    seller_id_str: str,
) -> int:
    if card.get("redeemed"):
        return pid
    if key_u and keys_index.get(key_u, {}).get("redeemed"):
        return pid
    if key_u and key_u in seen_keys:
        return pid
    card_num = str(card.get("card", card.get("card_number", "")))
    bin6 = card_num[:6] if len(card_num) >= 6 else "000000"
    price = card.get("price", 0)
    try:
        price = float(price)
    except (TypeError, ValueError):
        price = 0.0
    pid += 1
    out.append(
        {
            "id": pid,
            "bin": bin6,
            "brand": brand_from_bin(bin6),
            "type": "CREDIT",
            "country": {"flag": "🇺🇸", "flagClass": "fi-us", "code": "US", "name": "USA"},
            "bank": "BANK",
            "base": "2026_US_Base",
            "refundable": True,
            "price": f"{price:.2f}",
            "key": key_u,
            "seller_id": seller_id_str,
            "full_info": card.get("full_info", card.get("full_text", "")),
        }
    )
    if key_u:
        seen_keys.add(key_u)
    return pid


def build_products() -> List[Dict[str, Any]]:
    """Shop listing from shop_stock.json plus any active keys not already listed (stays in sync with keys.json)."""
    stock = load_json(STOCK_FILE, {})
    keys_index = load_json(KEYS_FILE, {})
    out: List[Dict[str, Any]] = []
    seen_keys: set = set()
    pid = 0
    for seller_id_str, cards in stock.items():
        for card in cards:
            key_u = (card.get("key") or "").strip().upper()
            pid = _append_product_row(out, seen_keys, keys_index, pid, card, key_u, seller_id_str)

    for key_raw, entry in keys_index.items():
        if entry.get("redeemed"):
            continue
        key_u = (key_raw or "").strip().upper()
        if not key_u or key_u in seen_keys:
            continue
        sid_raw = entry.get("seller_id", 0)
        try:
            seller_id_str = str(int(sid_raw))
        except (TypeError, ValueError):
            seller_id_str = str(sid_raw) if sid_raw is not None else "0"
        pid = _append_product_row(out, seen_keys, keys_index, pid, entry, key_u, seller_id_str)

    return out


def redeem_key_for_site(key_raw: str, buyer_username: str) -> Optional[Dict[str, Any]]:
    """Mark a shop key redeemed (same data as Telegram redeem). Returns card_info or None."""
    key = (key_raw or "").strip().upper()
    if not key:
        return None
    data_keys = load_json(KEYS_FILE, {})
    if key not in data_keys or data_keys[key].get("redeemed"):
        return None
    card_info = dict(data_keys[key])
    seller_id = card_info.get("seller_id", 0)
    try:
        seller_id_int = int(seller_id)
    except (TypeError, ValueError):
        seller_id_int = 0

    now = datetime.now(timezone.utc).isoformat()
    data_keys[key]["redeemed"] = True
    data_keys[key]["redeemed_by_name"] = buyer_username
    data_keys[key]["redeemed_at"] = now

    stock = load_json(STOCK_FILE, {})
    sid = str(seller_id_int)
    if sid in stock:
        for card in stock[sid]:
            if str(card.get("key", "")).upper() == key:
                card["redeemed"] = True
                card["redeemed_by_name"] = buyer_username
                card["redeemed_at"] = now
                break

    redeemed = load_json(REDEEMED_FILE, {})
    redeemed[key] = {
        "card_info": card_info,
        "buyer_name": buyer_username,
        "redeem_time": now,
        "source": "site",
    }
    save_json(KEYS_FILE, data_keys)
    save_json(STOCK_FILE, stock)
    save_json(REDEEMED_FILE, redeemed)
    return card_info


def _price_ok(stored: Any, posted: Any) -> bool:
    try:
        a = float(stored)
        b = float(posted)
    except (TypeError, ValueError):
        return False
    return abs(a - b) < 0.02


def load_games() -> Dict[str, Any]:
    return load_json(
        GAMES_FILE,
        {"dice_bets": [], "dice_history": [], "bj_matches": [], "bj_history": []},
    )


def save_games(g: Dict[str, Any]) -> None:
    save_json(GAMES_FILE, g)


# --- Routes: core ---


@app.route("/")
def index():
    path = os.path.join(app.static_folder, "index.html")
    if os.path.isfile(path):
        return send_from_directory(app.static_folder, "index.html")
    return (
        jsonify(
            {
                "ok": True,
                "message": "Pluxo API is running. Add static/index.html to serve the site from this app.",
            }
        ),
        200,
    )


@app.route("/api/register", methods=["POST"])
def api_register():
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    username = norm_user(body.get("username", ""))
    email = (body.get("email") or "").strip()
    if not username:
        return jsonify({"error": "username required"}), 400
    users = load_json(USERS_FILE, {})
    ensure_user(users, username)
    append_log(f"site register {username} email={email!r}")
    return jsonify({"success": True, "username": username})


@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    """Site account: username + email + password (stored hashed). Balance lives in admin_users.json."""
    body = request.get_json(force=True, silent=True) or {}
    username = norm_user(body.get("username", ""))
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not username or len(username) < 2:
        return jsonify({"error": "Invalid username"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    site_users = load_json(SITE_USERS_FILE, {})
    if username in site_users:
        return jsonify({"error": "Username already registered"}), 400
    site_users[username] = {
        "email": email,
        "password_hash": generate_password_hash(password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save_json(SITE_USERS_FILE, site_users)
    bal_users = load_json(USERS_FILE, {})
    ensure_user(bal_users, username)
    append_log(f"site auth register {username}")
    try:
        telegram_broadcast_html(
            f"🆕 <b>New site signup</b>\nUser: <code>{html_module.escape(username)}</code>"
        )
    except Exception:
        pass
    return jsonify({"success": True, "username": username})


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    body = request.get_json(force=True, silent=True) or {}
    username = norm_user(body.get("username", ""))
    password = body.get("password") or ""
    if not username or not password:
        return jsonify({"error": "Invalid credentials"}), 401
    site_users = load_json(SITE_USERS_FILE, {})
    rec = site_users.get(username)
    if not rec or not check_password_hash(rec.get("password_hash", ""), password):
        return jsonify({"error": "Invalid credentials"}), 401
    bal_users = load_json(USERS_FILE, {})
    u = ensure_user(bal_users, username)
    bal = float(u.get("balance", 0))
    tr = float(u.get("totalRecharge", 0))
    return jsonify(
        {
            "success": True,
            "username": username,
            "balance": bal,
            "totalRecharge": tr,
            "email": rec.get("email", ""),
        }
    )


@app.route("/api/orders/<username>", methods=["GET"])
def api_orders_get(username: str):
    bad = require_secret()
    if bad:
        return bad
    key = norm_user(username)
    orders_map = load_json(SITE_ORDERS_FILE, {})
    return jsonify({"success": True, "orders": orders_map.get(key, [])})


@app.route("/api/orders/add", methods=["POST"])
def api_orders_add():
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    username = norm_user(body.get("username", ""))
    order = body.get("order")
    if not username or not order:
        return jsonify({"error": "invalid"}), 400
    orders_map = load_json(SITE_ORDERS_FILE, {})
    orders_map.setdefault(username, []).append(order)
    save_json(SITE_ORDERS_FILE, orders_map)
    return jsonify({"success": True})


@app.route("/api/checkout", methods=["POST"])
def api_checkout():
    """Atomic: verify balance, redeem keys, deduct balance, save orders, notify Telegram."""
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    username = norm_user(body.get("username", ""))
    items: List[Dict[str, Any]] = body.get("items") or []
    if not username or not items:
        return jsonify({"error": "invalid cart"}), 400

    total = 0.0
    normalized_items: List[Dict[str, Any]] = []
    for it in items:
        try:
            price = float(it.get("price", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid price"}), 400
        k = (it.get("key") or "").strip().upper()
        if not k:
            return jsonify({"error": "missing key"}), 400
        total += price
        normalized_items.append(
            {
                "key": k,
                "price": price,
                "bin": it.get("bin") or "000000",
                "bank": it.get("bank") or "UNKNOWN",
                "base": it.get("base") or "",
                "refundable": it.get("refundable", True),
            }
        )

    data_keys = load_json(KEYS_FILE, {})
    for it in normalized_items:
        k = it["key"]
        if k not in data_keys or data_keys[k].get("redeemed"):
            return jsonify({"error": f"Invalid or sold key: {k}"}), 400
        if not _price_ok(data_keys[k].get("price", 0), it["price"]):
            return jsonify({"error": f"Price mismatch for key {k}"}), 400

    users = load_json(USERS_FILE, {})
    u = ensure_user(users, username)
    bal = float(u.get("balance", 0))
    if bal + 1e-9 < total:
        return jsonify({"error": "Insufficient balance"}), 400

    orders_map = load_json(SITE_ORDERS_FILE, {})
    new_rows: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for it in normalized_items:
        card_info = redeem_key_for_site(it["key"], username)
        if not card_info:
            logger.error("Redeem failed for %s after validation — check stock/keys", it["key"])
            return jsonify({"error": "Could not complete purchase. Try again or contact support."}), 500
        full_text = card_info.get("full_info") or card_info.get("full_text", "")
        oid = "ORD" + now.strftime("%Y%m%d%H%M%S") + str(random.randint(100, 999))
        row = {
            "id": oid,
            "key": it["key"],
            "price": it["price"],
            "bin": it["bin"],
            "bank": it["bank"],
            "base": it["base"],
            "refundable": it["refundable"],
            "purchaseDate": now.isoformat(),
            "full_info": full_text,
        }
        new_rows.append(row)
        orders_map.setdefault(username, []).append(row)

    u["balance"] = round(bal - total, 2)
    save_json(USERS_FILE, users)
    save_json(SITE_ORDERS_FILE, orders_map)

    purchases = load_json(PURCHASES_FILE, [])
    purchases.append(
        {
            "username": username,
            "item": f"Web checkout ({len(new_rows)} items)",
            "amount": round(total, 2),
            "ts": now.isoformat(),
            "actor_id": 0,
        }
    )
    save_json(PURCHASES_FILE, purchases)
    append_log(f"site checkout {username} ${total:.2f} keys={len(new_rows)}")

    safe_u = html_module.escape(username)
    msg = (
        "🛒 <b>Purchase (site)</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 Username: <code>{safe_u}</code>\n"
        f"📦 Items: {len(new_rows)}\n"
        f"💵 Total: ${total:,.2f}\n"
        f"📅 Date: {now.strftime('%Y-%m-%d')}\n"
        f"🕐 Time: {now.strftime('%H:%M:%S')} UTC\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    telegram_broadcast_html(msg)

    return jsonify(
        {
            "success": True,
            "newBalance": u["balance"],
            "orders": orders_map.get(username, []),
        }
    )


@app.route("/api/balance/<username>", methods=["GET"])
def api_balance_get(username: str):
    bad = require_secret()
    if bad:
        return bad
    key = norm_user(username)
    users = load_json(USERS_FILE, {})
    if key not in users:
        return jsonify({"success": True, "balance": 0.0, "totalRecharge": 0.0})
    u = users[key]
    bal = float(u.get("balance", 0))
    tr = float(u.get("totalRecharge", 0))
    return jsonify({"success": True, "balance": bal, "totalRecharge": tr})


@app.route("/api/admin/balances", methods=["GET"])
def api_admin_balances():
    bad = require_secret()
    if bad:
        return bad
    users = load_json(USERS_FILE, {})
    rows = []
    for name, u in sorted(users.items()):
        rows.append(
            {
                "username": name,
                "balance": float(u.get("balance", 0)),
                "totalRecharge": float(u.get("totalRecharge", 0)),
                "registered_at": u.get("registered_at", ""),
            }
        )
    return jsonify({"success": True, "users": rows})


@app.route("/api/balance/update", methods=["POST"])
def api_balance_update():
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    key = norm_user(body.get("username", ""))
    action = (body.get("action") or "").lower()
    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount"}), 400
    if not key or action not in ("add", "subtract") or amount < 0:
        return jsonify({"error": "invalid request"}), 400

    users = load_json(USERS_FILE, {})
    u = ensure_user(users, key)
    bal = float(u.get("balance", 0))
    if action == "subtract":
        if bal < amount - 1e-9:
            return jsonify({"error": "Insufficient balance"}), 400
        bal = round(bal - amount, 2)
    else:
        bal = round(bal + amount, 2)
        u["totalRecharge"] = round(float(u.get("totalRecharge", 0)) + amount, 2)
    u["balance"] = bal
    save_json(USERS_FILE, users)
    reason = body.get("reason", "")
    append_log(f"site balance_update {key} {action} ${amount:.2f} reason={reason!r} -> ${bal:.2f}")
    return jsonify({"success": True, "newBalance": bal})


@app.route("/api/balance-requests/submit", methods=["POST"])
def api_balance_request_submit():
    """User submits a crypto top-up request (pending admin approval). Proof optional (data URL)."""
    body = request.get_json(force=True, silent=True) or {}
    username = norm_user(body.get("username", ""))
    password = body.get("password") or ""
    if not username or not password:
        return jsonify({"error": "Invalid credentials"}), 401
    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount"}), 400
    payment_method = (body.get("paymentMethod") or "").lower().strip()
    if payment_method not in ("btc", "ltc"):
        return jsonify({"error": "invalid payment method"}), 400
    if amount < 10 or amount > 100_000:
        return jsonify({"error": "invalid amount"}), 400

    site_users = load_json(SITE_USERS_FILE, {})
    rec = site_users.get(username)
    if not rec or not check_password_hash(rec.get("password_hash", ""), password):
        return jsonify({"error": "Invalid credentials"}), 401

    proof = body.get("paymentProofDataUrl")
    proof_name = (body.get("paymentProofFileName") or "").strip()[:255] or None
    if proof is not None:
        if not isinstance(proof, str):
            return jsonify({"error": "invalid proof"}), 400
        if len(proof) > 6_500_000:
            return jsonify({"error": "proof too large"}), 400
        if not (
            proof.startswith("data:image/")
            or proof.startswith("data:application/pdf")
        ):
            return jsonify({"error": "proof must be an image or PDF data URL"}), 400

    bonus = (body.get("bonusCode") or "").strip() or None
    request_id = "BR" + uuid.uuid4().hex[:16].upper()
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": request_id,
        "username": username,
        "userId": username,
        "amount": amount,
        "paymentMethod": payment_method,
        "bonusCode": bonus,
        "paymentProofDataUrl": proof,
        "paymentProofFileName": proof_name,
        "status": "pending",
        "createdAt": now,
        "approvedBy": None,
        "approvedAt": None,
        "adminComment": None,
    }
    data = load_json(SITE_BALANCE_REQUESTS_FILE, {})
    data[request_id] = row
    save_json(SITE_BALANCE_REQUESTS_FILE, data)
    append_log(f"site balance_request submit {username} ${amount:.2f} {payment_method} id={request_id}")
    try:
        telegram_broadcast_html(
            f"💰 <b>Balance request (site)</b>\n"
            f"User: <code>{html_module.escape(username)}</code>\n"
            f"Amount: ${amount:,.2f}\n"
            f"Method: {html_module.escape(payment_method)}\n"
            f"ID: <code>{html_module.escape(request_id)}</code>"
        )
    except Exception:
        pass
    return jsonify({"success": True, "requestId": request_id})


@app.route("/api/admin/balance-requests", methods=["GET"])
def api_admin_balance_requests():
    bad = require_secret()
    if bad:
        return bad
    data = load_json(SITE_BALANCE_REQUESTS_FILE, {})
    rows = list(data.values())
    rows.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
    return jsonify({"success": True, "requests": rows})


@app.route("/api/admin/balance-requests/<request_id>/approve", methods=["POST"])
def api_admin_balance_request_approve(request_id: str):
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    admin_username = norm_user(body.get("adminUsername", ""))
    admin_comment = body.get("adminComment")

    data = load_json(SITE_BALANCE_REQUESTS_FILE, {})
    req = data.get(request_id)
    if not req or req.get("status") != "pending":
        return jsonify({"error": "not found or not pending"}), 400

    try:
        amount = float(req.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid stored amount"}), 400
    if amount <= 0:
        return jsonify({"error": "invalid amount"}), 400

    username = norm_user(req.get("username", ""))
    if not username:
        return jsonify({"error": "invalid request"}), 400

    users = load_json(USERS_FILE, {})
    u = ensure_user(users, username)
    bal = float(u.get("balance", 0))
    u["balance"] = round(bal + amount, 2)
    u["totalRecharge"] = round(float(u.get("totalRecharge", 0)) + amount, 2)
    save_json(USERS_FILE, users)

    req["status"] = "approved"
    req["approvedBy"] = admin_username or "admin"
    req["approvedAt"] = datetime.now(timezone.utc).isoformat()
    req["adminComment"] = admin_comment
    data[request_id] = req
    save_json(SITE_BALANCE_REQUESTS_FILE, data)

    append_log(
        f"site balance_request approve {request_id} {username} ${amount:.2f} by {admin_username!r}"
    )
    try:
        telegram_broadcast_html(
            f"✅ <b>Balance approved (site)</b>\n"
            f"User: <code>{html_module.escape(username)}</code>\n"
            f"+${amount:,.2f}\n"
            f"ID: <code>{html_module.escape(request_id)}</code>"
        )
    except Exception:
        pass
    return jsonify({"success": True, "newBalance": u["balance"]})


@app.route("/api/admin/balance-requests/<request_id>/reject", methods=["POST"])
def api_admin_balance_request_reject(request_id: str):
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    admin_username = norm_user(body.get("adminUsername", ""))
    admin_comment = body.get("adminComment")

    data = load_json(SITE_BALANCE_REQUESTS_FILE, {})
    req = data.get(request_id)
    if not req or req.get("status") != "pending":
        return jsonify({"error": "not found or not pending"}), 400

    req["status"] = "rejected"
    req["approvedBy"] = admin_username or "admin"
    req["approvedAt"] = datetime.now(timezone.utc).isoformat()
    req["adminComment"] = admin_comment
    data[request_id] = req
    save_json(SITE_BALANCE_REQUESTS_FILE, data)
    append_log(f"site balance_request reject {request_id} by {admin_username!r}")
    return jsonify({"success": True})


@app.route("/api/purchase/notify", methods=["POST"])
def api_purchase_notify():
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    user = norm_user(body.get("username", ""))
    count = int(body.get("item_count", 0) or 0)
    try:
        total = float(body.get("total_amount", 0))
    except (TypeError, ValueError):
        total = 0.0
    now = datetime.now(timezone.utc)
    append_log(f"site purchase {user} items={count} total=${total:.2f}")

    purchases = load_json(PURCHASES_FILE, [])
    purchases.append(
        {
            "username": user,
            "item": f"Web checkout ({count} items)",
            "amount": round(total, 2),
            "ts": now.isoformat(),
            "actor_id": 0,
        }
    )
    save_json(PURCHASES_FILE, purchases)

    msg = (
        "🛒 <b>Purchase (site)</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 Username: <code>{user}</code>\n"
        f"📦 Items: {count}\n"
        f"💵 Total: ${total:,.2f}\n"
        f"📅 Date: {now.strftime('%Y-%m-%d')}\n"
        f"🕐 Time: {now.strftime('%H:%M:%S')} UTC\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    telegram_broadcast_html(msg)
    return jsonify({"success": True})


@app.route("/api/products", methods=["GET"])
def api_products():
    # Public catalog; optional secret if you lock it down later
    products = build_products()
    return jsonify(products)


# --- Dice ---


def _dice_winner_name(c: str, o: str, c_roll: int, o_roll: int) -> str:
    if c_roll > o_roll:
        return c
    if o_roll > c_roll:
        return o
    return "tie"


@app.route("/api/games/dice/create", methods=["POST"])
def dice_create():
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    creator = norm_user(body.get("creator", ""))
    creator_name = body.get("creatorName") or creator
    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount"}), 400
    if not creator or amount < 1 or amount > 25:
        return jsonify({"error": "invalid bet"}), 400

    users = load_json(USERS_FILE, {})
    u = ensure_user(users, creator)
    bal = float(u.get("balance", 0))
    if bal < amount:
        return jsonify({"error": "Insufficient balance"}), 400
    u["balance"] = round(bal - amount, 2)
    save_json(USERS_FILE, users)

    g = load_games()
    bet_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    bet = {
        "id": bet_id,
        "creator": creator,
        "creatorName": creator_name,
        "amount": amount,
        "status": "waiting",
    }
    g["dice_bets"].append(bet)
    save_games(g)
    return jsonify({"bet": bet, "newBalance": u["balance"]})


@app.route("/api/games/dice/bets", methods=["GET"])
def dice_bets():
    bad = require_secret()
    if bad:
        return bad
    g = load_games()
    return jsonify({"bets": g.get("dice_bets", [])})


@app.route("/api/games/dice/history", methods=["GET"])
def dice_history():
    bad = require_secret()
    if bad:
        return bad
    g = load_games()
    return jsonify({"history": g.get("dice_history", [])})


@app.route("/api/games/dice/accept", methods=["POST"])
def dice_accept():
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    bet_id = body.get("betId", "")
    opponent = norm_user(body.get("opponent", ""))
    opponent_name = body.get("opponentName") or opponent
    g = load_games()
    bets: List[Dict] = g.get("dice_bets", [])
    bet = next((b for b in bets if b.get("id") == bet_id), None)
    if not bet or bet.get("status") != "waiting":
        return jsonify({"error": "Bet not found"}), 404
    if bet.get("creator") == opponent:
        return jsonify({"error": "Cannot accept own bet"}), 400
    amount = float(bet.get("amount", 0))

    users = load_json(USERS_FILE, {})
    uo = ensure_user(users, opponent)
    bal_o = float(uo.get("balance", 0))
    if bal_o < amount:
        return jsonify({"error": "Insufficient balance"}), 400
    uo["balance"] = round(bal_o - amount, 2)

    c_roll = random.randint(1, 6)
    o_roll = random.randint(1, 6)
    creator = bet["creator"]
    winner = _dice_winner_name(creator, opponent, c_roll, o_roll)

    uc = ensure_user(users, creator)
    if winner == "tie":
        uc["balance"] = round(float(uc.get("balance", 0)) + amount, 2)
        uo["balance"] = round(float(uo.get("balance", 0)) + amount, 2)
    elif winner == creator:
        uc["balance"] = round(float(uc.get("balance", 0)) + 2 * amount, 2)
    else:
        uo["balance"] = round(float(uo.get("balance", 0)) + 2 * amount, 2)

    save_json(USERS_FILE, users)

    win_name = "Tie"
    if winner == creator:
        win_name = bet.get("creatorName") or creator
    elif winner == opponent:
        win_name = opponent_name

    completed_at = datetime.now(timezone.utc).isoformat()
    result = {
        "id": bet_id,
        "creator": creator,
        "opponent": opponent,
        "creatorName": bet.get("creatorName"),
        "opponentName": opponent_name,
        "amount": amount,
        "creatorRoll": c_roll,
        "opponentRoll": o_roll,
        "winner": winner,
        "winnerName": win_name,
        "completedAt": completed_at,
        "creatorBalanceAfter": float(uc.get("balance", 0)),
        "opponentBalanceAfter": float(uo.get("balance", 0)),
    }

    g["dice_bets"] = [b for b in bets if b.get("id") != bet_id]
    hist = g.setdefault("dice_history", [])
    hist.append(result)
    while len(hist) > 200:
        hist.pop(0)
    save_games(g)

    viewer_bal = float(uo.get("balance", 0))
    return jsonify({"result": result, "viewerBalance": viewer_bal})


@app.route("/api/games/dice/cancel", methods=["POST"])
def dice_cancel():
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    bet_id = body.get("betId", "")
    username = norm_user(body.get("username", ""))
    g = load_games()
    bets: List[Dict] = g.get("dice_bets", [])
    bet = next((b for b in bets if b.get("id") == bet_id), None)
    if not bet or bet.get("status") != "waiting":
        return jsonify({"error": "Bet not found"}), 404
    if bet.get("creator") != username:
        return jsonify({"error": "Not your bet"}), 403
    amount = float(bet.get("amount", 0))
    users = load_json(USERS_FILE, {})
    u = ensure_user(users, username)
    u["balance"] = round(float(u.get("balance", 0)) + amount, 2)
    save_json(USERS_FILE, users)
    g["dice_bets"] = [b for b in bets if b.get("id") != bet_id]
    save_games(g)
    return jsonify({"newBalance": u["balance"], "amount": amount})


# --- Blackjack (simplified scores) ---


@app.route("/api/games/blackjack/create", methods=["POST"])
def bj_create():
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    creator = norm_user(body.get("creator", ""))
    creator_name = body.get("creatorName") or creator
    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount"}), 400
    if not creator or amount < 1 or amount > 25:
        return jsonify({"error": "invalid bet"}), 400
    users = load_json(USERS_FILE, {})
    u = ensure_user(users, creator)
    bal = float(u.get("balance", 0))
    if bal < amount:
        return jsonify({"error": "Insufficient balance"}), 400
    u["balance"] = round(bal - amount, 2)
    save_json(USERS_FILE, users)
    g = load_games()
    mid = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    match = {
        "id": mid,
        "creator": creator,
        "creatorName": creator_name,
        "amount": amount,
        "status": "waiting",
    }
    g["bj_matches"].append(match)
    save_games(g)
    return jsonify({"newBalance": u["balance"], "match": match})


@app.route("/api/games/blackjack/matches", methods=["GET"])
def bj_matches():
    bad = require_secret()
    if bad:
        return bad
    g = load_games()
    return jsonify({"matches": g.get("bj_matches", [])})


@app.route("/api/games/blackjack/history", methods=["GET"])
def bj_history():
    bad = require_secret()
    if bad:
        return bad
    g = load_games()
    return jsonify({"history": g.get("bj_history", [])})


@app.route("/api/games/blackjack/join", methods=["POST"])
def bj_join():
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    match_id = body.get("matchId", "")
    opponent = norm_user(body.get("opponent", ""))
    opponent_name = body.get("opponentName") or opponent
    g = load_games()
    matches: List[Dict] = g.get("bj_matches", [])
    m = next((x for x in matches if x.get("id") == match_id), None)
    if not m or m.get("status") != "waiting":
        return jsonify({"error": "Match not found"}), 404
    if m.get("creator") == opponent:
        return jsonify({"error": "Cannot join own match"}), 400
    amount = float(m.get("amount", 0))
    users = load_json(USERS_FILE, {})
    uo = ensure_user(users, opponent)
    if float(uo.get("balance", 0)) < amount:
        return jsonify({"error": "Insufficient balance"}), 400
    uo["balance"] = round(float(uo.get("balance", 0)) - amount, 2)

    # Simple hand: 17–21 inclusive
    cs = random.randint(17, 21)
    os_ = random.randint(17, 21)
    creator = m["creator"]
    winner = "tie"
    if cs > os_:
        winner = creator
    elif os_ > cs:
        winner = opponent

    uc = ensure_user(users, creator)
    if winner == "tie":
        uc["balance"] = round(float(uc.get("balance", 0)) + amount, 2)
        uo["balance"] = round(float(uo.get("balance", 0)) + amount, 2)
    elif winner == creator:
        uc["balance"] = round(float(uc.get("balance", 0)) + 2 * amount, 2)
    else:
        uo["balance"] = round(float(uo.get("balance", 0)) + 2 * amount, 2)
    save_json(USERS_FILE, users)

    win_name = "Tie"
    if winner == creator:
        win_name = m.get("creatorName") or creator
    elif winner == opponent:
        win_name = opponent_name

    completed_at = datetime.now(timezone.utc).isoformat()
    result = {
        "id": match_id,
        "creator": creator,
        "opponent": opponent,
        "creatorName": m.get("creatorName"),
        "opponentName": opponent_name,
        "amount": amount,
        "creatorScore": cs,
        "opponentScore": os_,
        "winner": winner,
        "winnerName": win_name,
        "completedAt": completed_at,
        "creatorBalanceAfter": float(uc.get("balance", 0)),
        "opponentBalanceAfter": float(uo.get("balance", 0)),
    }
    g["bj_matches"] = [x for x in matches if x.get("id") != match_id]
    hist = g.setdefault("bj_history", [])
    hist.append(result)
    while len(hist) > 200:
        hist.pop(0)
    save_games(g)
    return jsonify({"result": result, "viewerBalance": float(uo.get("balance", 0))})


@app.route("/api/games/blackjack/cancel", methods=["POST"])
def bj_cancel():
    bad = require_secret()
    if bad:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    match_id = body.get("matchId", "")
    username = norm_user(body.get("username", ""))
    g = load_games()
    matches: List[Dict] = g.get("bj_matches", [])
    m = next((x for x in matches if x.get("id") == match_id), None)
    if not m or m.get("status") != "waiting":
        return jsonify({"error": "Match not found"}), 404
    if m.get("creator") != username:
        return jsonify({"error": "Not your match"}), 403
    amount = float(m.get("amount", 0))
    users = load_json(USERS_FILE, {})
    u = ensure_user(users, username)
    u["balance"] = round(float(u.get("balance", 0)) + amount, 2)
    save_json(USERS_FILE, users)
    g["bj_matches"] = [x for x in matches if x.get("id") != match_id]
    save_games(g)
    return jsonify({"newBalance": u["balance"], "amount": amount})


def run_server() -> None:
    port = int(os.environ.get("PORT", 5000))
    logger.info("Starting Flask API on 0.0.0.0:%s", port)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
