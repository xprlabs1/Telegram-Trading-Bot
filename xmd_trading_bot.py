#!/usr/bin/env python3
"""
XPR Network Telegram Trading Bot
=================================
Commands:
  /start                        - Welcome message
  /help                         - List commands
  /balance                      - Show XBTC and XMD balances
  /buy [percentage]             - Market buy XBTC using XMD (default 100%)
  /sell [percentage]            - Market sell XBTC to XMD (default 100%)
  /panicsell                    - Immediately sell 100% XBTC

Setup:
  1. Fill in config.ini with your XPR credentials
  2. Set TELEGRAM_TOKEN and ALLOWED_USER_IDS in config.ini
  3. pip install python-telegram-bot pyeoskit requests
  4. python xpr_trading_bot.py
"""

import json
import logging
import configparser
import requests
from math import pow

from pyeoskit import eosapi, wallet
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

config = configparser.ConfigParser()
config.read('config.ini')

PRIVATE_KEY      = config.get('credentials', 'private_key')
USERNAME         = config.get('credentials', 'username')
TELEGRAM_TOKEN   = config.get('telegram', 'token')
ALLOWED_USER_IDS = [int(x) for x in config.get('telegram', 'allowed_user_ids').split(',') if x.strip()]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# XPR / MetalX constants
# ---------------------------------------------------------------------------

eosapi.set_node('https://mainnet-rpc.api.protondex.com')
wallet.import_key(USERNAME, PRIVATE_KEY)

PERMISSION = {USERNAME: 'active'}

SUBMIT_URL         = "https://mainnet.api.protondex.com/dex/v1/orders/submit"
BALANCE_URL        = "https://mainnet.api.protondex.com/dex/v1/account/balances"
HEADERS            = {"content-type": "application/json", "Accept-Charset": "UTF-8"}

BID_TOKEN_CONTRACT = 'xtokens'
BID_TOKEN_SYMBOL   = 'XBTC'
BID_TOKEN_PRECISION = 8

ASK_TOKEN_CONTRACT = 'xmd.token'
ASK_TOKEN_SYMBOL   = 'XMD'
ASK_TOKEN_PRECISION = 6

MARKET_ID  = 2
FILL_TYPE  = 1   # IOC (Immediate-Or-Cancel)
ORDER_TYPE = 1   # Market order

# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def restricted(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ALLOWED_USER_IDS:
            logger.warning("Unauthorized access by user %s", update.effective_user.id)
            await update.message.reply_text("⛔ Unauthorized.")
            return
        return await func(update, context)
    return wrapper

# ---------------------------------------------------------------------------
# Core trading logic (extracted from original script)
# ---------------------------------------------------------------------------

def fetch_balances() -> dict:
    """Return {symbol: amount} for all account balances."""
    response = requests.get(f"{BALANCE_URL}?account={USERNAME}", timeout=10)
    response.raise_for_status()
    data = response.json().get("data", [])
    return {item["currency"]: float(item["amount"]) for item in data}


def get_token_balance(symbol: str, precision: int) -> int:
    """Return balance in raw integer units (amount * 10^precision)."""
    balances = fetch_balances()
    amount = balances.get(symbol, 0.0)
    return int(amount * pow(10, precision))


def place_order(order_side: int, order_amount: int) -> dict:
    """
    Build and submit a market order to MetalX DEX.
    order_side: 1 = buy XBTC, 2 = sell XBTC
    order_amount: raw integer units of the token being spent
    """
    if order_side == 1:
        # Buying XBTC — spend XMD
        token_contract = ASK_TOKEN_CONTRACT
        token_symbol   = ASK_TOKEN_SYMBOL
        token_precision = ASK_TOKEN_PRECISION
        price = int(9223372036854775806)   # max price — take any offer
    else:
        # Selling XBTC — spend XBTC
        token_contract = BID_TOKEN_CONTRACT
        token_symbol   = BID_TOKEN_SYMBOL
        token_precision = BID_TOKEN_PRECISION
        price = 1                           # min price — take any bid

    quantity_str = f"{order_amount / pow(10, token_precision):.{token_precision}f} {token_symbol}"

    args1 = {
        'from': USERNAME,
        'to': 'dex',
        'quantity': quantity_str,
        'memo': ''
    }
    args2 = {
        'market_id': MARKET_ID,
        'account': USERNAME,
        'order_type': ORDER_TYPE,
        'order_side': order_side,
        'fill_type': FILL_TYPE,
        'bid_symbol': {
            'sym': f'{BID_TOKEN_PRECISION},{BID_TOKEN_SYMBOL}',
            'contract': BID_TOKEN_CONTRACT
        },
        'ask_symbol': {
            'sym': f'{ASK_TOKEN_PRECISION},{ASK_TOKEN_SYMBOL}',
            'contract': ASK_TOKEN_CONTRACT
        },
        'referrer': '',
        'quantity': order_amount,
        'price': price,
        'trigger_price': 0
    }
    args3 = {
        'q_size': 20,
        'show_error_msg': 0
    }

    a1 = [token_contract, 'transfer', args1, PERMISSION]
    a2 = ['dex', 'placeorder', args2, PERMISSION]
    a3 = ['dex', 'process', args3, PERMISSION]

    info = eosapi.get_info()
    final_tx = eosapi.generate_packed_transaction(
        [a1, a2, a3],
        60,
        info['last_irreversible_block_id'],
        info['chain_id']
    )
    mtx = json.loads(final_tx)

    payload = {
        "serialized_tx_hex": mtx["packed_trx"],
        "signatures": mtx["signatures"]
    }
    response = requests.post(SUBMIT_URL, json=payload, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *XPR Network Trading Bot*\n\nType /help to see commands.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*Commands:*\n\n"
        "`/balance` — show XBTC and XMD balances\n"
        "`/buy` — buy 100% XBTC with XMD\n"
        "`/buy 50` — buy with 50% of XMD balance\n"
        "`/sell` — sell 100% XBTC to XMD\n"
        "`/sell 25` — sell 25% of XBTC balance\n"
        "`/panicsell` — immediately sell all XBTC\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@restricted
async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching balances...")
    try:
        balances = fetch_balances()
        xbtc = balances.get(BID_TOKEN_SYMBOL, 0.0)
        xmd  = balances.get(ASK_TOKEN_SYMBOL, 0.0)
        msg = (
            f"💰 *Account: `{USERNAME}`*\n\n"
            f"`XBTC`: `{xbtc:.{BID_TOKEN_PRECISION}f}`\n"
            f"`XMD`:  `{xmd:.{ASK_TOKEN_PRECISION}f}`"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.exception("balance error")
        await update.message.reply_text(f"❌ Error fetching balance: {e}")


@restricted
async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    percentage = 100
    if context.args:
        try:
            percentage = int(context.args[0])
            if percentage not in (25, 50, 75, 100):
                await update.message.reply_text("❌ Percentage must be 25, 50, 75, or 100.")
                return
        except ValueError:
            await update.message.reply_text("❌ Usage: `/buy [25|50|75|100]`", parse_mode="Markdown")
            return

    await update.message.reply_text(f"⏳ Placing buy order ({percentage}% of XMD balance)...")
    try:
        total = get_token_balance(ASK_TOKEN_SYMBOL, ASK_TOKEN_PRECISION)
        if total == 0:
            await update.message.reply_text("❌ Insufficient XMD balance.")
            return
        amount = int(total * (percentage / 100.0))
        qty_str = f"{amount / pow(10, ASK_TOKEN_PRECISION):.{ASK_TOKEN_PRECISION}f} {ASK_TOKEN_SYMBOL}"
        result = place_order(order_side=1, order_amount=amount)
        await update.message.reply_text(
            f"✅ *Buy order placed*\n"
            f"Spent: `{qty_str}`\n"
            f"Response: `{json.dumps(result)}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("buy error")
        await update.message.reply_text(f"❌ Buy failed: {e}")


@restricted
async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    percentage = 100
    if context.args:
        try:
            percentage = int(context.args[0])
            if percentage not in (25, 50, 75, 100):
                await update.message.reply_text("❌ Percentage must be 25, 50, 75, or 100.")
                return
        except ValueError:
            await update.message.reply_text("❌ Usage: `/sell [25|50|75|100]`", parse_mode="Markdown")
            return

    await update.message.reply_text(f"⏳ Placing sell order ({percentage}% of XBTC balance)...")
    try:
        total = get_token_balance(BID_TOKEN_SYMBOL, BID_TOKEN_PRECISION)
        if total == 0:
            await update.message.reply_text("❌ Insufficient XBTC balance.")
            return
        amount = int(total * (percentage / 100.0))
        qty_str = f"{amount / pow(10, BID_TOKEN_PRECISION):.{BID_TOKEN_PRECISION}f} {BID_TOKEN_SYMBOL}"
        result = place_order(order_side=2, order_amount=amount)
        await update.message.reply_text(
            f"✅ *Sell order placed*\n"
            f"Sold: `{qty_str}`\n"
            f"Response: `{json.dumps(result)}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("sell error")
        await update.message.reply_text(f"❌ Sell failed: {e}")


@restricted
async def cmd_panicsell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚨 *PANIC SELL* — selling 100% XBTC immediately...", parse_mode="Markdown")
    try:
        total = get_token_balance(BID_TOKEN_SYMBOL, BID_TOKEN_PRECISION)
        if total == 0:
            await update.message.reply_text("❌ No XBTC balance to sell.")
            return
        qty_str = f"{total / pow(10, BID_TOKEN_PRECISION):.{BID_TOKEN_PRECISION}f} {BID_TOKEN_SYMBOL}"
        result = place_order(order_side=2, order_amount=total)
        await update.message.reply_text(
            f"✅ *Panic sell executed*\n"
            f"Sold: `{qty_str}`\n"
            f"Response: `{json.dumps(result)}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("panicsell error")
        await update.message.reply_text(f"❌ Panic sell failed: {e}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception", exc_info=context.error)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("balance",   cmd_balance))
    app.add_handler(CommandHandler("buy",       cmd_buy))
    app.add_handler(CommandHandler("sell",      cmd_sell))
    app.add_handler(CommandHandler("panicsell", cmd_panicsell))

    app.add_error_handler(error_handler)

    logger.info("XPR Trading Bot started. Polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
