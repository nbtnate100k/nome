import logging
import json
import random
import string
import os
import re
import html
from datetime import datetime, timezone
from typing import Dict, List, Optional

from telegram import Update, User, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "8480830954:AAGI3tK2hnYuOBgaf6FRhaEwmBmwTvGv6uY")
CHANNEL_USERNAME = "@pluxo_official"
OWNER_ID = 7173346586

# Data storage files
ADMINS_FILE = "admins.json"
SELLERS_FILE = "sellers.json"
STOCK_FILE = "shop_stock.json"
KEYS_FILE = "keys.json"
REDEEMED_FILE = "redeemed.json"
USERS_FILE = "admin_users.json"
PURCHASES_FILE = "purchases.json"
LOGS_FILE = "action_logs.json"
STATE_FILE = "bot_state.json"
MAX_LOG_ENTRIES = 200

def generate_key(length=16):
    """Generate a random alphanumeric key"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def load_json(filename, default):
    """Load JSON data from file"""
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default

def save_json(filename, data):
    """Save data to JSON file"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def load_data():
    """Load all data from files"""
    return {
        'admins': load_json(ADMINS_FILE, [OWNER_ID, 7909346512]),
        'sellers': load_json(SELLERS_FILE, [OWNER_ID, 7909346512]),
        'stock': load_json(STOCK_FILE, {}),  # {seller_id: [{card_info}]}
        'keys': load_json(KEYS_FILE, {}),     # {key: {card_info, seller_id, price}}
        'redeemed': load_json(REDEEMED_FILE, {}),  # {key: {card_info, buyer_id, time}}
        'admin_users': load_json(USERS_FILE, {}),  # {lowercase_username: {balance, registered_at}}
        'purchases': load_json(PURCHASES_FILE, []),
        'action_logs': load_json(LOGS_FILE, []),
        'bot_state': load_json(STATE_FILE, {"lockdown": False}),
    }

def save_admins(admins):
    save_json(ADMINS_FILE, admins)

def save_sellers(sellers):
    save_json(SELLERS_FILE, sellers)

def save_stock(stock):
    save_json(STOCK_FILE, stock)

def save_keys(keys):
    save_json(KEYS_FILE, keys)

def save_redeemed(redeemed):
    save_json(REDEEMED_FILE, redeemed)

def save_admin_users(admin_users):
    save_json(USERS_FILE, admin_users)

def save_purchases(purchases):
    save_json(PURCHASES_FILE, purchases)

def save_action_logs(logs):
    save_json(LOGS_FILE, logs)

def save_bot_state(state):
    save_json(STATE_FILE, state)

def norm_username(name: str) -> str:
    if not name:
        return ""
    return name.strip().lstrip("@").lower()

def is_locked_for_user(data: dict, user_id: int) -> bool:
    if not data.get("bot_state", {}).get("lockdown"):
        return False
    return user_id != OWNER_ID

async def reply_locked(update: Update):
    await update.message.reply_text(
        "🔒 System is locked. Only the owner can use commands right now."
    )

def append_action_log(data: dict, line: str):
    logs: List = data.setdefault("action_logs", [])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logs.append(f"[{ts}] {line}")
    while len(logs) > MAX_LOG_ENTRIES:
        logs.pop(0)
    save_action_logs(logs)

def ensure_admin_user(data: dict, username_key: str) -> dict:
    users = data["admin_users"]
    if username_key not in users:
        users[username_key] = {
            "balance": 0.0,
            "registered_at": datetime.now(timezone.utc).strftime("%m/%d/%y"),
        }
        save_admin_users(users)
    return users[username_key]

def admin_help_text(user: User, user_id: int) -> str:
    role = "OWNER" if user_id == OWNER_ID else "ADMIN"
    display = escape_html(user.full_name or "Admin")
    return (
        "🔐 <b>PLUXO Admin Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 Welcome, {display}\n"
        f"🆔 Your ID: <code>{user_id}</code>\n"
        f"👑 Role: {role}\n\n"
        "📋 <b>Commands:</b>\n\n"
        "💰 <b>Balance Management:</b>\n"
        "/balance &lt;username&gt; - View user balance\n"
        "/setbalance &lt;username&gt; &lt;amount&gt; - Set balance\n"
        "/addbalance &lt;username&gt; &lt;amount&gt; - Add to balance\n"
        "/removebalance &lt;username&gt; &lt;amount&gt; - Remove from balance\n"
        "/allbalances - View all balances\n"
        "/users - List all registered users\n\n"
        "🛒 <b>Purchase Tracking:</b>\n"
        "/addpurchase &lt;username&gt; &lt;item&gt; &lt;amount&gt; - Log purchase\n"
        "/purchases &lt;username&gt; - View user purchases\n"
        "/recentpurchases - Recent purchases (last 20)\n\n"
        "👥 <b>Admin Management (Owner only):</b>\n"
        "/addadmin &lt;userid&gt; - Add admin\n"
        "/removeadmin &lt;userid&gt; - Remove admin\n"
        "/admins - List all admins\n\n"
        "🔒 <b>System:</b>\n"
        "/lockdown - Lock/unlock system (Owner only)\n"
        "/logs - View recent action logs\n"
        "/status - System status\n\n"
        "📦 <b>Shop (sellers):</b>\n"
        "/stock, /mystock, /redeem - Stock &amp; keys\n"
        "/viewallstock, /allkeys, /stats - Admin shop tools"
    )

def is_admin(user_id: int, admins_list: List[int]) -> bool:
    return user_id in admins_list

def is_seller(user_id: int, sellers_list: List[int]) -> bool:
    return user_id in sellers_list

def escape_html(text: str) -> str:
    return html.escape(str(text))

def mask_card(card_number: str) -> str:
    """Mask card number showing first 6 and last 4 digits"""
    if len(card_number) >= 10:
        return card_number[:6] + "******" + card_number[-4:]
    return card_number

def parse_bulk_cards(text: str) -> List[Dict]:
    """Parse bulk pipe-delimited cards (card|mm|yyyy|cvv)"""
    cards = []
    lines = text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Match: 5355851164846467|02|2026|358
        match = re.match(r'^(\d{15,16})\|(\d{1,2})\|(\d{4})\|(\d{3,4})$', line)
        if match:
            card_number = match.group(1)
            exp_month = match.group(2).zfill(2)
            exp_year = match.group(3)
            cvv = match.group(4)
            
            if len(card_number) == 15:
                card_number = '0' + card_number
            
            cards.append({
                'card_number': card_number,
                'exp_month': exp_month,
                'exp_year': exp_year,
                'cvv': cvv,
                'full_text': f"{card_number}|{exp_month}|{exp_year}|{cvv}",
                'name': '',
                'address': '',
                'city_state_zip': '',
                'country': ''
            })
    
    return cards

def parse_multiline_cards(text: str) -> List[Dict]:
    """Parse multi-line cards with address info (card exp cvv + 4 lines of info)"""
    cards = []
    lines = text.strip().split('\n')
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        
        # Match: 4145670692391812 01/29 651
        match = re.match(r'^(\d{15,16})\s+(\d{2})/(\d{2})\s+(\d{3,4})$', line)
        if match:
            card_number = match.group(1)
            exp_month = match.group(2)
            exp_year_short = match.group(3)
            cvv = match.group(4)
            
            # Convert 2-digit year to 4-digit
            exp_year = '20' + exp_year_short
            
            if len(card_number) == 15:
                card_number = '0' + card_number
            
            # Get next 4 lines for address info
            name = lines[i + 1].strip() if i + 1 < len(lines) else ''
            address = lines[i + 2].strip() if i + 2 < len(lines) else ''
            city_state_zip = lines[i + 3].strip() if i + 3 < len(lines) else ''
            country = lines[i + 4].strip() if i + 4 < len(lines) else ''
            
            full_text = f"{card_number} {exp_month}/{exp_year_short} {cvv}\n{name}\n{address}\n{city_state_zip}\n{country}"
            
            cards.append({
                'card_number': card_number,
                'exp_month': exp_month,
                'exp_year': exp_year,
                'cvv': cvv,
                'full_text': full_text,
                'name': name,
                'address': address,
                'city_state_zip': city_state_zip,
                'country': country
            })
            
            i += 5  # Skip to next card block
            continue
        
        i += 1
    
    return cards

def parse_all_formats(text: str) -> List[Dict]:
    """Try both card formats and return parsed cards"""
    # Try pipe format first
    cards = parse_bulk_cards(text)
    if cards:
        return cards
    
    # Try multi-line format
    cards = parse_multiline_cards(text)
    if cards:
        return cards
    
    return []

# ==================== START COMMAND ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome / admin command list"""
    data = context.bot_data
    user = update.effective_user
    user_id = user.id

    if is_admin(user_id, data["admins"]):
        if is_locked_for_user(data, user_id):
            await update.message.reply_html(
                "🔐 <b>PLUXO Admin Bot</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "🔒 <b>System is locked.</b>\n"
                "Only the owner can use commands right now."
            )
            return
        await update.message.reply_html(admin_help_text(user, user_id))
        return

    if is_locked_for_user(data, user_id):
        await update.message.reply_text(
            "🔒 This bot is temporarily unavailable. Please try again later."
        )
        return

    name = escape_html(user.first_name or "there")
    await update.message.reply_html(
        f"Hi {name}! Welcome to the pluxo.net stock bot.\n\n"
        f"Use /redeem with your key. Join {CHANNEL_USERNAME} for updates."
    )

# ==================== STOCK COMMAND ====================

async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add stock with price and generate keys: /stock [price] [cards]"""
    data = context.bot_data
    user_id = update.effective_user.id

    if is_locked_for_user(data, user_id):
        await reply_locked(update)
        return
    
    if not is_seller(user_id, data['sellers']) and not is_admin(user_id, data['admins']):
        await update.message.reply_text("❌ You are not authorized to add stock.")
        return
    
    message_text = update.message.text
    
    # Parse: /stock 15 cards... (without /bulk)
    # Handle both "/stock 15 card" and "/stock 15\ncard" formats
    lines = message_text.split('\n', 1)
    first_line = lines[0].strip()
    
    # Extract price from first line
    first_line_parts = first_line.split()
    if len(first_line_parts) < 2:
        await update.message.reply_html(
            "📦 <b>Stock Command Usage:</b>\n\n"
            "<b>Format 1 (Pipe):</b>\n"
            "<code>/stock 15\n"
            "5355851164846467|02|2026|358\n"
            "5355851675248468|02|2026|737</code>\n\n"
            "<b>Format 2 (Multi-line with address):</b>\n"
            "<code>/stock 15\n"
            "4145670692391812 01/29 651\n"
            "Jonica Somaguera\n"
            "5541 Windemere Cir\n"
            "Trinity, NO  37370\n"
            "United States</code>\n\n"
            "⚠️ <b>Remember:</b> Price must be on the FIRST line after /stock"
        )
        return
    
    # Validate price is a reasonable number (not a card number)
    price_str = first_line_parts[1].lstrip("$").strip()
    if not price_str.replace('.', '').isdigit():
        await update.message.reply_html(
            "❌ Invalid price format!\n\n"
            "Price must be a number (e.g., 5, 15, 12.50)\n\n"
            "<b>Correct format:</b>\n"
            "<code>/stock 15\n"
            "[card data here]</code>"
        )
        return
    
    try:
        price = float(price_str)
        # Check if price looks like a card number (too large)
        if price > 10000:
            await update.message.reply_html(
                "❌ <b>Price looks wrong!</b>\n\n"
                "You entered: ${:,.0f}\n\n".format(price) +
                "Did you forget to put the price on the first line?\n\n"
                "<b>Correct format:</b>\n"
                "<code>/stock 15\n"
                "4145670692391812 01/29 651\n"
                "Name\n"
                "Address</code>"
            )
            return
    except ValueError:
        await update.message.reply_html(
            "❌ Invalid price!\n\n"
            "<b>Correct format:</b>\n"
            "<code>/stock 15\n"
            "[card data]</code>"
        )
        return
    
    # Get cards text (everything after price on first line, or all subsequent lines)
    if len(first_line_parts) > 2:
        # Cards on same line as price
        cards_text = ' '.join(first_line_parts[2:])
    elif len(lines) > 1:
        # Cards on new lines
        cards_text = lines[1]
    else:
        cards_text = ""
    
    # Parse cards (try both formats)
    parsed_cards = parse_all_formats(cards_text)
    
    if not parsed_cards:
        await update.message.reply_text(
            "❌ No valid cards found!\n\n"
            "Supported formats:\n"
            "1. Pipe: 5355851675248468|02|2026|737\n"
            "2. Multi-line:\n"
            "4145670692391812 01/29 651\n"
            "Name\nAddress\nCity State Zip\nCountry"
        )
        return
    
    # Initialize seller's stock if needed
    seller_id_str = str(user_id)
    if seller_id_str not in data['stock']:
        data['stock'][seller_id_str] = []
    
    # Generate keys and add to stock
    generated_keys = []
    for card in parsed_cards:
        key = generate_key()
        
        # Ensure unique key
        while key in data['keys']:
            key = generate_key()
        
        # Use consistent format with existing data
        card_entry = {
            'card': card['card_number'],
            'expiry': card['exp_month'],
            'cvv': card['cvv'],
            'price': price,
            'key': key,
            'added_at': datetime.now().isoformat(),
            'redeemed': False,
            'full_info': card['full_text'],
            'exp_year': card['exp_year']
        }
        
        # Add extra address fields if present
        if card.get('name'):
            card_entry['name'] = card['name']
        if card.get('address'):
            card_entry['address'] = card['address']
        if card.get('city_state_zip'):
            card_entry['city_state_zip'] = card['city_state_zip']
        if card.get('country'):
            card_entry['country'] = card['country']
        
        # Add to seller's stock
        data['stock'][seller_id_str].append(card_entry)
        
        # Add to keys lookup with seller_id
        key_entry = card_entry.copy()
        key_entry['seller_id'] = user_id
        data['keys'][key] = key_entry
        
        generated_keys.append({
            'masked': mask_card(card['card_number']),
            'key': key
        })
    
    # Save data
    save_stock(data['stock'])
    save_keys(data['keys'])
    
    # Build response
    response = f"✅ Added {len(parsed_cards)} cards at ${price} each!\n\n"
    response += "🔑 <b>Generated Keys:</b>\n\n"
    
    for item in generated_keys:
        response += f"<code>{item['masked']}</code>\n└ Key: <code>{item['key']}</code>\n\n"
    
    # Count total stock for this seller (only non-redeemed)
    seller_stock = data['stock'].get(seller_id_str, [])
    active_stock = [c for c in seller_stock if not c.get('redeemed', False)]
    response += f"📊 Your total stock: {len(active_stock)} cards"
    
    await update.message.reply_html(response)

# ==================== REDEEM COMMAND ====================

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem a card using a key"""
    data = context.bot_data
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name

    if is_locked_for_user(data, user_id):
        await reply_locked(update)
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /redeem [KEY]\nExample: /redeem HM6ABOFE0Z14JRXP")
        return
    
    key = context.args[0].upper()
    
    # Check if key exists and not already redeemed
    if key not in data['keys']:
        await update.message.reply_text("❌ Invalid key!")
        return
    
    card_info = data['keys'][key]
    
    # Check if already redeemed
    if card_info.get('redeemed', False):
        await update.message.reply_text("❌ This key has already been redeemed!")
        return
    
    # Get card info
    seller_id = card_info.get('seller_id', OWNER_ID)
    price = card_info.get('price', 0)
    card_number = card_info.get('card', card_info.get('card_number', ''))
    
    # Get full card text
    full_text = card_info.get('full_info', card_info.get('full_text', ''))
    if not full_text:
        # Build from components
        exp = card_info.get('expiry', card_info.get('exp_month', ''))
        exp_year = card_info.get('exp_year', '')
        cvv = card_info.get('cvv', '')
        if exp_year and len(exp_year) == 4:
            full_text = f"{card_number}|{exp}|{exp_year}|{cvv}"
        else:
            full_text = f"{card_number} {exp} {cvv}"
    
    # Mark as redeemed in keys
    data['keys'][key]['redeemed'] = True
    data['keys'][key]['redeemed_by'] = user_id
    data['keys'][key]['redeemed_at'] = datetime.now().isoformat()
    
    # Mark as redeemed in seller's stock
    seller_id_str = str(seller_id)
    if seller_id_str in data['stock']:
        for card in data['stock'][seller_id_str]:
            if card.get('key') == key:
                card['redeemed'] = True
                card['redeemed_by'] = user_id
                card['redeemed_at'] = datetime.now().isoformat()
                break
    
    # Add to redeemed log
    redeem_time = datetime.now().isoformat()
    data['redeemed'][key] = {
        'card_info': card_info,
        'buyer_id': user_id,
        'buyer_name': user_name,
        'redeem_time': redeem_time
    }
    
    # Save all data
    save_keys(data['keys'])
    save_stock(data['stock'])
    save_redeemed(data['redeemed'])
    
    # Send card to user
    await update.message.reply_html(
        f"✅ <b>Key Redeemed Successfully!</b>\n\n"
        f"💳 <b>Your Card:</b>\n"
        f"<code>{full_text}</code>\n\n"
        f"💰 Value: ${price}\n"
        f"⏰ Redeemed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    
    # Notify admins + seller (deduped) — purchase-style alert
    now_utc = datetime.now(timezone.utc)
    date_str = now_utc.strftime("%Y-%m-%d")
    time_str = f"{now_utc.strftime('%H:%M:%S')} UTC"
    tg_user = update.effective_user
    if tg_user.username:
        buyer_label = f"@{escape_html(tg_user.username)}"
    else:
        buyer_label = escape_html(user_name or str(user_id))
    try:
        total_fmt = f"${float(price):,.2f}"
    except (TypeError, ValueError):
        total_fmt = f"${price}"

    purchase_notice = (
        "🛒 <b>Purchase Made</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 Username: {buyer_label}\n"
        "📦 Items: 1\n"
        f"💵 Total: {total_fmt}\n"
        f"📅 Date: {date_str}\n"
        f"🕐 Time: {time_str}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔑 Key: <code>{escape_html(key)}</code>\n"
        f"💳 Card: <code>{escape_html(mask_card(card_number))}</code>\n"
        f"🆔 Buyer ID: <code>{user_id}</code>"
    )

    notify_ids = set(data["admins"])
    notify_ids.add(int(seller_id))

    for chat_id in notify_ids:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=purchase_notice,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Failed to notify {chat_id}: {e}")

# ==================== MYSTOCK COMMAND ====================

async def mystock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View your own stock"""
    data = context.bot_data
    user_id = update.effective_user.id

    if is_locked_for_user(data, user_id):
        await reply_locked(update)
        return
    
    if not is_seller(user_id, data['sellers']) and not is_admin(user_id, data['admins']):
        await update.message.reply_text("❌ You are not a seller.")
        return
    
    seller_stock = data['stock'].get(str(user_id), [])
    # Filter out redeemed cards
    active_stock = [c for c in seller_stock if not c.get('redeemed', False)]
    
    if not active_stock:
        await update.message.reply_text("📭 You have no stock.")
        return
    
    response = f"📦 <b>Your Stock:</b> {len(active_stock)} cards\n\n"
    
    for i, card in enumerate(active_stock[:20], 1):
        card_num = card.get('card', card.get('card_number', 'Unknown'))
        price = card.get('price', 0)
        key = card.get('key', 'N/A')
        response += f"{i}. <code>{mask_card(card_num)}</code> ${price}\n"
        response += f"   Key: <code>{key}</code>\n"
    
    if len(active_stock) > 20:
        response += f"\n... and {len(active_stock) - 20} more cards"
    
    await update.message.reply_html(response)

# ==================== VIEWALLSTOCK COMMAND ====================

async def viewallstock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all sellers' stock (admin only)"""
    data = context.bot_data
    user_id = update.effective_user.id

    if is_locked_for_user(data, user_id):
        await reply_locked(update)
        return
    
    if not is_admin(user_id, data['admins']):
        await update.message.reply_text("❌ Admin only command.")
        return
    
    if not data['stock']:
        await update.message.reply_text("📭 No stock available.")
        return
    
    response = "📊 <b>All Stock Overview:</b>\n\n"
    total_cards = 0
    
    for seller_id_str, cards in data['stock'].items():
        # Filter out redeemed cards
        active_cards = [c for c in cards if not c.get('redeemed', False)]
        if active_cards:
            response += f"<b>Seller {seller_id_str}:</b> {len(active_cards)} cards available\n"
            for card in active_cards[:5]:
                card_num = card.get('card', card.get('card_number', 'Unknown'))
                price = card.get('price', 0)
                response += f"  └ <code>{mask_card(card_num)}</code> | ${price}\n"
            if len(active_cards) > 5:
                response += f"  └ ... and {len(active_cards) - 5} more\n"
            response += "\n"
            total_cards += len(active_cards)
    
    if total_cards == 0:
        await update.message.reply_text("📭 No stock available.")
        return
    
    response += f"📊 <b>Total Available:</b> {total_cards} cards"
    
    await update.message.reply_html(response)

# ==================== ALLKEYS COMMAND ====================

async def allkeys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all active keys (admin only)"""
    data = context.bot_data
    user_id = update.effective_user.id

    if is_locked_for_user(data, user_id):
        await reply_locked(update)
        return
    
    if not is_admin(user_id, data['admins']):
        await update.message.reply_text("❌ Admin only command.")
        return
    
    # Filter only non-redeemed keys
    active_keys = {k: v for k, v in data['keys'].items() if not v.get('redeemed', False)}
    
    if not active_keys:
        await update.message.reply_text("📭 No active keys.")
        return
    
    response = f"🔑 <b>Active Keys:</b> {len(active_keys)}\n\n"
    
    for i, (key, card_info) in enumerate(list(active_keys.items())[:30], 1):
        card_num = card_info.get('card', card_info.get('card_number', 'Unknown'))
        price = card_info.get('price', 0)
        response += f"{i}. <code>{key}</code>\n"
        response += f"   {mask_card(card_num)} ${price}\n"
    
    if len(active_keys) > 30:
        response += f"\n... and {len(active_keys) - 30} more keys"
    
    await update.message.reply_html(response)

# ==================== SELLER MANAGEMENT ====================

async def addseller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a seller (admin only)"""
    data = context.bot_data
    user_id = update.effective_user.id

    if is_locked_for_user(data, user_id):
        await reply_locked(update)
        return
    
    if not is_admin(user_id, data['admins']):
        await update.message.reply_text("❌ Admin only command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /addseller [user_id]")
        return
    
    try:
        new_seller_id = int(context.args[0])
        if new_seller_id not in data['sellers']:
            data['sellers'].append(new_seller_id)
            save_sellers(data['sellers'])
            await update.message.reply_text(f"✅ Added seller: {new_seller_id}")
        else:
            await update.message.reply_text("❌ User is already a seller.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def removeseller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a seller (admin only)"""
    data = context.bot_data
    user_id = update.effective_user.id

    if is_locked_for_user(data, user_id):
        await reply_locked(update)
        return
    
    if not is_admin(user_id, data['admins']):
        await update.message.reply_text("❌ Admin only command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /removeseller [user_id]")
        return
    
    try:
        seller_id = int(context.args[0])
        if seller_id in data['sellers']:
            data['sellers'].remove(seller_id)
            save_sellers(data['sellers'])
            await update.message.reply_text(f"✅ Removed seller: {seller_id}")
        else:
            await update.message.reply_text("❌ User is not a seller.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def listsellers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all sellers (admin only)"""
    data = context.bot_data
    user_id = update.effective_user.id

    if is_locked_for_user(data, user_id):
        await reply_locked(update)
        return
    
    if not is_admin(user_id, data['admins']):
        await update.message.reply_text("❌ Admin only command.")
        return
    
    if not data['sellers']:
        await update.message.reply_text("📭 No sellers registered.")
        return
    
    response = f"👥 <b>Sellers:</b> {len(data['sellers'])}\n\n"
    for seller_id in data['sellers']:
        seller_cards = data['stock'].get(str(seller_id), [])
        stock_count = len([c for c in seller_cards if not c.get('redeemed', False)])
        response += f"• <code>{seller_id}</code> - {stock_count} cards\n"
    
    await update.message.reply_html(response)

# ==================== STATS COMMAND ====================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics (admin only)"""
    data = context.bot_data
    user_id = update.effective_user.id

    if is_locked_for_user(data, user_id):
        await reply_locked(update)
        return
    
    if not is_admin(user_id, data['admins']):
        await update.message.reply_text("❌ Admin only command.")
        return
    
    # Count only non-redeemed stock
    total_stock = 0
    for cards in data['stock'].values():
        total_stock += len([c for c in cards if not c.get('redeemed', False)])
    
    # Count only non-redeemed keys
    active_keys = len([k for k, v in data['keys'].items() if not v.get('redeemed', False)])
    total_redeemed = len(data['redeemed'])
    total_sellers = len(data['sellers'])
    
    response = (
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👑 Admins: {len(data['admins'])}\n"
        f"👥 Sellers: {total_sellers}\n"
        f"📦 Total Stock: {total_stock} cards\n"
        f"🔑 Active Keys: {active_keys}\n"
        f"✅ Total Redeemed: {total_redeemed}\n"
    )
    
    await update.message.reply_html(response)

# ==================== ADDADMIN COMMAND ====================

async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new admin"""
    data = context.bot_data
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ Only owner can add admins.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /addadmin [user_id]")
        return
    
    try:
        new_admin_id = int(context.args[0])
        if new_admin_id not in data['admins']:
            data['admins'].append(new_admin_id)
            save_admins(data['admins'])
            await update.message.reply_text(f"✅ Added admin: {new_admin_id}")
        else:
            await update.message.reply_text("❌ User is already an admin.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

# ==================== ADMIN: BALANCE & PURCHASES ====================

def _parse_amount(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    if not context.args:
        await update.message.reply_text("Usage: /balance username")
        return
    key = norm_username(context.args[0])
    if key not in data["admin_users"]:
        await update.message.reply_text(f"❌ User '{key}' not found.")
        return
    bal = float(data["admin_users"][key].get("balance", 0))
    await update.message.reply_html(
        f"👤 <b>{escape_html(key)}</b>\n💰 Balance: <b>${bal:,.2f}</b>"
    )


async def cmd_setbalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setbalance username amount")
        return
    key = norm_username(context.args[0])
    amt = _parse_amount(context.args[1])
    if amt is None or amt < 0:
        await update.message.reply_text("❌ Invalid amount.")
        return
    ensure_admin_user(data, key)
    data["admin_users"][key]["balance"] = round(amt, 2)
    save_admin_users(data["admin_users"])
    append_action_log(data, f"Admin {uid} setbalance {key} -> ${amt:.2f}")
    await update.message.reply_html(
        "✅ <b>Balance Set</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: <b>{escape_html(key)}</b>\n"
        f"💰 New Balance: <b>${amt:,.2f}</b>"
    )


async def cmd_addbalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addbalance username amount")
        return
    key = norm_username(context.args[0])
    amt = _parse_amount(context.args[1])
    if amt is None or amt <= 0:
        await update.message.reply_text("❌ Invalid amount.")
        return
    u = ensure_admin_user(data, key)
    new_bal = round(float(u.get("balance", 0)) + amt, 2)
    data["admin_users"][key]["balance"] = new_bal
    save_admin_users(data["admin_users"])
    append_action_log(data, f"Admin {uid} addbalance {key} +${amt:.2f} -> ${new_bal:.2f}")
    await update.message.reply_html(
        "✅ <b>Balance Added</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: <b>{escape_html(key)}</b>\n"
        f"➕ Added: <b>+${amt:,.2f}</b>\n"
        f"💰 New Balance: <b>${new_bal:,.2f}</b>"
    )


async def cmd_removebalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /removebalance username amount")
        return
    key = norm_username(context.args[0])
    amt = _parse_amount(context.args[1])
    if amt is None or amt <= 0:
        await update.message.reply_text("❌ Invalid amount.")
        return
    if key not in data["admin_users"]:
        await update.message.reply_text(f"❌ User '{key}' not found.")
        return
    cur = float(data["admin_users"][key].get("balance", 0))
    new_bal = max(0.0, round(cur - amt, 2))
    data["admin_users"][key]["balance"] = new_bal
    save_admin_users(data["admin_users"])
    append_action_log(data, f"Admin {uid} removebalance {key} -${amt:.2f} -> ${new_bal:.2f}")
    await update.message.reply_html(
        "✅ <b>Balance Updated</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: <b>{escape_html(key)}</b>\n"
        f"➖ Removed: <b>${amt:,.2f}</b>\n"
        f"💰 New Balance: <b>${new_bal:,.2f}</b>"
    )


async def cmd_allbalances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    if not data["admin_users"]:
        await update.message.reply_text("📭 No user balances yet.")
        return
    lines = []
    for name in sorted(data["admin_users"].keys()):
        bal = float(data["admin_users"][name].get("balance", 0))
        reg = data["admin_users"][name].get("registered_at", "?")
        lines.append(f"• <b>{escape_html(name)}</b> — ${bal:,.2f} ({escape_html(reg)})")
    body = "\n".join(lines)
    await update.message.reply_html(
        f"💰 <b>All Balances</b>\n━━━━━━━━━━━━━━━━━━\n{body}\n━━━━━━━━━━━━━━━━━━\n"
        f"📊 Total users: {len(data['admin_users'])}"
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    if not data["admin_users"]:
        await update.message.reply_text("📭 No registered users.")
        return
    lines = []
    for i, name in enumerate(sorted(data["admin_users"].keys()), 1):
        bal = float(data["admin_users"][name].get("balance", 0))
        reg = data["admin_users"][name].get("registered_at", "?")
        lines.append(f"{i}. {escape_html(name)} — ${bal:,.2f} ({escape_html(reg)})")
    body = "\n".join(lines)
    await update.message.reply_html(
        f"👥 <b>Registered Users</b>\n━━━━━━━━━━━━━━━━━━\n{body}\n━━━━━━━━━━━━━━━━━━\n"
        f"📊 Total Users: {len(data['admin_users'])}"
    )


async def cmd_addpurchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage: /addpurchase username item amount\n"
            "Example: /addpurchase nbttest CC_Stock 15.00"
        )
        return
    key = norm_username(context.args[0])
    amount = _parse_amount(context.args[-1])
    if amount is None or amount < 0:
        await update.message.reply_text("❌ Invalid amount.")
        return
    item = " ".join(context.args[1:-1]).strip()
    if not item:
        await update.message.reply_text("❌ Item name required.")
        return
    ensure_admin_user(data, key)
    ts = datetime.now(timezone.utc)
    entry = {
        "username": key,
        "item": item,
        "amount": round(amount, 2),
        "ts": ts.isoformat(),
        "actor_id": uid,
    }
    data["purchases"].append(entry)
    save_purchases(data["purchases"])
    append_action_log(
        data, f"Admin {uid} addpurchase {key} item={item!r} ${amount:.2f}"
    )
    await update.message.reply_html(
        "🛒 <b>Purchase Logged</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: <b>{escape_html(key)}</b>\n"
        f"📦 Item: {escape_html(item)}\n"
        f"💵 Amount: <b>${amount:,.2f}</b>\n"
        f"📅 {ts.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )


async def cmd_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    if not context.args:
        await update.message.reply_text("Usage: /purchases username")
        return
    key = norm_username(context.args[0])
    rows = [p for p in data["purchases"] if p.get("username") == key]
    if not rows:
        await update.message.reply_text(f"📭 No purchases for '{key}'.")
        return
    lines = []
    for p in rows[-30:]:
        ts = p.get("ts", "")
        lines.append(
            f"• {escape_html(p.get('item', ''))} — ${float(p.get('amount', 0)):,.2f} — {escape_html(ts[:19])}"
        )
    await update.message.reply_html(
        f"🛒 <b>Purchases: {escape_html(key)}</b>\n━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(lines)
    )


async def cmd_recentpurchases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    recent = data["purchases"][-20:][::-1]
    if not recent:
        await update.message.reply_text("📭 No purchases logged yet.")
        return
    lines = []
    for p in recent:
        lines.append(
            f"• <b>{escape_html(p.get('username', ''))}</b> — "
            f"{escape_html(p.get('item', ''))} — ${float(p.get('amount', 0)):,.2f}"
        )
    await update.message.reply_html(
        "🛒 <b>Recent Purchases (20)</b>\n━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines)
    )


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("❌ Only owner can remove admins.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeadmin user_id")
        return
    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    if rid == OWNER_ID:
        await update.message.reply_text("❌ Cannot remove the owner.")
        return
    if rid not in data["admins"]:
        await update.message.reply_text("❌ That user is not an admin.")
        return
    data["admins"].remove(rid)
    save_admins(data["admins"])
    append_action_log(data, f"Owner {uid} removeadmin {rid}")
    await update.message.reply_text(f"✅ Removed admin: {rid}")


async def cmd_list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    lines = []
    for aid in data["admins"]:
        role = "OWNER" if aid == OWNER_ID else "admin"
        lines.append(f"• <code>{aid}</code> ({role})")
    await update.message.reply_html(
        "👥 <b>Admins</b>\n━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(lines)
        + f"\n━━━━━━━━━━━━━━━━━━\n📊 Total: {len(data['admins'])}"
    )


async def cmd_lockdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("❌ Owner only command.")
        return
    st = data.setdefault("bot_state", {"lockdown": False})
    st["lockdown"] = not bool(st.get("lockdown"))
    save_bot_state(st)
    append_action_log(data, f"Owner {uid} lockdown={'ON' if st['lockdown'] else 'OFF'}")
    await update.message.reply_text(
        f"🔒 Lockdown is now: {'ON (non-owner blocked)' if st['lockdown'] else 'OFF'}"
    )


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    logs = data.get("action_logs", [])
    tail = logs[-30:]
    if not tail:
        await update.message.reply_text("📭 No action logs yet.")
        return
    text = "\n".join(escape_html(x) for x in tail)
    await update.message.reply_html(f"📜 <b>Recent logs</b>\n<code>{text}</code>")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    uid = update.effective_user.id
    if not is_admin(uid, data["admins"]):
        await update.message.reply_text("❌ Admin only command.")
        return
    if is_locked_for_user(data, uid):
        await reply_locked(update)
        return
    locked = bool(data.get("bot_state", {}).get("lockdown"))
    nu = len(data.get("admin_users", {}))
    np = len(data.get("purchases", []))
    total_stock = sum(
        len([c for c in cards if not c.get("redeemed", False)])
        for cards in data.get("stock", {}).values()
    )
    active_keys = len(
        [k for k, v in data.get("keys", {}).items() if not v.get("redeemed", False)]
    )
    await update.message.reply_html(
        "📡 <b>System Status</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🔒 Lockdown: <b>{'ON' if locked else 'OFF'}</b>\n"
        f"👥 Tracked users: {nu}\n"
        f"🛒 Purchase rows: {np}\n"
        f"📦 Shop stock (active): {total_stock}\n"
        f"🔑 Active keys: {active_keys}\n"
        f"✅ Redeemed: {len(data.get('redeemed', {}))}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕐 UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
    )

# ==================== ERROR HANDLER ====================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")

async def post_init(application: Application):
    """Initialize bot data after startup"""
    data = load_data()
    application.bot_data.update(data)
    if OWNER_ID not in data["admins"]:
        data["admins"].append(OWNER_ID)
        save_admins(data["admins"])
    
    logger.info(f"✅ Loaded {len(data['admins'])} admins")
    logger.info(f"✅ Loaded {len(data['sellers'])} sellers")
    logger.info(f"✅ Loaded {len(data['admin_users'])} balance users")
    logger.info(f"✅ Loaded {len(data['keys'])} active keys")
    logger.info(f"✅ Loaded {len(data['redeemed'])} redeemed keys")

# ==================== MAIN FUNCTION ====================

def main():
    """Start the bot."""
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # User commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("redeem", redeem))
    
    # Seller commands
    application.add_handler(CommandHandler("stock", stock))
    application.add_handler(CommandHandler("mystock", mystock))
    
    # Admin: balances & purchases
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("setbalance", cmd_setbalance))
    application.add_handler(CommandHandler("addbalance", cmd_addbalance))
    application.add_handler(CommandHandler("removebalance", cmd_removebalance))
    application.add_handler(CommandHandler("allbalances", cmd_allbalances))
    application.add_handler(CommandHandler("users", cmd_users))
    application.add_handler(CommandHandler("addpurchase", cmd_addpurchase))
    application.add_handler(CommandHandler("purchases", cmd_purchases))
    application.add_handler(CommandHandler("recentpurchases", cmd_recentpurchases))
    application.add_handler(CommandHandler("removeadmin", cmd_removeadmin))
    application.add_handler(CommandHandler("admins", cmd_list_admins))
    application.add_handler(CommandHandler("lockdown", cmd_lockdown))
    application.add_handler(CommandHandler("logs", cmd_logs))
    application.add_handler(CommandHandler("status", cmd_status))

    # Admin commands (shop)
    application.add_handler(CommandHandler("viewallstock", viewallstock))
    application.add_handler(CommandHandler("allkeys", allkeys))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("addadmin", addadmin))
    application.add_handler(CommandHandler("addseller", addseller))
    application.add_handler(CommandHandler("removeseller", removeseller))
    application.add_handler(CommandHandler("listsellers", listsellers))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    print("🤖 PluxoShopBot is starting...")
    print(f"📢 Channel: {CHANNEL_USERNAME}")
    print(f"👑 Owner ID: {OWNER_ID}")
    print("🔑 Key-based card shop system")
    print("Bot is running. Press Ctrl+C to stop.")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
