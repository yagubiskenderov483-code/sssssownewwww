# bot.py — NFT Deal Bot | aiogram 3.7+ | Business Mode
import asyncio
import logging
import random
import re
import sqlite3
import string
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN    = "8469717125:AAEbUIanMZltA742IAXyLU7x79QcAtlTMZA"
ADMIN_ID = 741904495

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp  = Dispatcher()

# ── БД ───────────────────────────────────────────────────────
db = sqlite3.connect("deals.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS deals (
    order_id        TEXT PRIMARY KEY,
    buyer_username  TEXT,
    seller_id       INTEGER,
    nft_url         TEXT,
    nft_slug        TEXT,
    nft_num         TEXT,
    amount          INTEGER,
    status          TEXT DEFAULT 'active',
    created_at      TEXT,
    biz_id          TEXT,
    chat_id         INTEGER,
    deal_msg_id     INTEGER
);
""")
db.commit()


def gen_order_id() -> str:
    return "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def parse_nft(url: str):
    m = re.match(r"https?://t\.me/nft/([A-Za-z]+)-(\d+)", url)
    return (m.group(1), m.group(2)) if m else (None, None)


def get_deal(order_id: str) -> dict | None:
    row = db.execute("SELECT * FROM deals WHERE order_id=?", (order_id,)).fetchone()
    return dict(row) if row else None


def upd(order_id: str, **kw):
    for k, v in kw.items():
        db.execute(f"UPDATE deals SET {k}=? WHERE order_id=?", (v, order_id))
    db.commit()


# ── Тексты ───────────────────────────────────────────────────
def deal_text(d: dict) -> str:
    return (
        f"<b>NFT Deal</b>\n\n"
        f"Ордер <b>#{d['order_id']}</b>\n\n"
        f"Покупатель зарезервировал <b>{d['amount']:,} ⭐️ Звёзд</b> через эскроу-систему "
        f"Telegram. Средства хранятся на специальном эскроу-счёте и будут автоматически "
        f"зачислены на ваш баланс Telegram Stars сразу после передачи подарка.\n\n"
        f"<b>Инструкция для завершения сделки:</b>\n"
        f"1. Передайте подарок пользователю: @{d['buyer_username']}\n"
        f"2. Нажмите «Передать NFT» и выберите {d['nft_slug']} #{d['nft_num']}\n"
        f"3. Подтвердите передачу подарка.\n\n"
        f"Telegram зафиксирует транзакцию и моментально зачислит "
        f"<b>{d['amount']:,} ⭐️ Звёзд</b> на ваш баланс. Резерв действует 24 часа."
    )


# ── Клавиатуры ───────────────────────────────────────────────
def deal_kb(d: dict):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="Передать NFT ↗",
        url=f"tg://resolve?domain={d['buyer_username']}"
    ))
    kb.row(InlineKeyboardButton(
        text="✅ Подтвердить передачу",
        callback_data=f"confirm:{d['order_id']}"
    ))
    return kb.as_markup()


def admin_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"adm_ok:{order_id}"),
        InlineKeyboardButton(text="❌ Не передан",  callback_data=f"adm_no:{order_id}")
    )
    return kb.as_markup()


def retry_kb(d: dict):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="Передать NFT ↗",
        url=f"tg://resolve?domain={d['buyer_username']}"
    ))
    kb.row(InlineKeyboardButton(
        text="✅ Подтвердить передачу",
        callback_data=f"confirm:{d['order_id']}"
    ))
    return kb.as_markup()


# ── .buy ─────────────────────────────────────────────────────
@dp.message(F.text.regexp(r"^\.buy\s+\S+\s+\d+"))
async def cmd_buy(message: Message):
    biz_id = message.business_connection_id

    if biz_id:
        try:
            biz_conn       = await bot.get_business_connection(biz_id)
            buyer_username = biz_conn.user.username or str(biz_conn.user.id)
        except Exception:
            buyer_username = "unknown"
    else:
        buyer_username = message.from_user.username or str(message.from_user.id)

    parts   = message.text.strip().split()
    nft_url = parts[1]
    amount  = int(parts[2])

    slug, num = parse_nft(nft_url)
    if not slug:
        return

    order_id = gen_order_id()
    chat_id  = message.chat.id

    db.execute("""
        INSERT INTO deals
          (order_id, buyer_username, seller_id, nft_url, nft_slug, nft_num,
           amount, status, created_at, biz_id, chat_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
    """, (order_id, buyer_username, chat_id, nft_url, slug, num,
          amount, datetime.now().isoformat(), biz_id or "", chat_id))
    db.commit()

    try:
        await message.delete()
    except Exception:
        pass

    d = get_deal(order_id)
    send_kw = {"business_connection_id": biz_id} if biz_id else {}

    msg = await bot.send_message(
        chat_id,
        deal_text(d),
        reply_markup=deal_kb(d),
        **send_kw
    )
    await bot.send_message(chat_id, nft_url, **send_kw)
    upd(order_id, deal_msg_id=msg.message_id)


# ── Подтвердить передачу ─────────────────────────────────────
@dp.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d or d['status'] != 'active':
        await call.answer("Ордер недоступен.", show_alert=True)
        return

    upd(order_id, status='pending_check')
    await call.answer(
        "⏳ Проверяем передачу, ожидайте до 2 минут.",
        show_alert=True
    )

    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🔔 <b>Запрос на подтверждение</b>\n\n"
            f"Ордер: <b>{d['order_id']}</b>\n"
            f"NFT: <b>{d['nft_slug']} #{d['nft_num']}</b>\n"
            f"Сумма: <b>{d['amount']:,} ⭐️</b>\n"
            f"Покупатель: @{d['buyer_username']}\n"
            f"Ссылка: {d['nft_url']}\n\n"
            f"Проверьте передачу NFT и подтвердите:",
            reply_markup=admin_kb(order_id)
        )
    except Exception as e:
        logging.error(f"admin notify: {e}")


# ── Админ: подтвердить ───────────────────────────────────────
@dp.callback_query(F.data.startswith("adm_ok:"))
async def cb_adm_ok(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d:
        return

    upd(order_id, status='completed')
    await call.answer("✅ Подтверждено.")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ Ордер {order_id} завершён.")

    send_kw = {"business_connection_id": d['biz_id']} if d['biz_id'] else {}
    try:
        await bot.send_message(
            d['chat_id'],
            f"✅ <b>Передача подтверждена!</b>\n\n"
            f"Ордер <b>{d['order_id']}</b>\n"
            f"<b>{d['amount']:,} ⭐️ Звёзд</b> зачислены на ваш баланс Telegram Stars.",
            **send_kw
        )
    except Exception as e:
        logging.error(e)


# ── Админ: не передан ────────────────────────────────────────
@dp.callback_query(F.data.startswith("adm_no:"))
async def cb_adm_no(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d:
        return

    upd(order_id, status='active')
    await call.answer("❌ Отклонено.")
    await call.message.edit_reply_markup(reply_markup=None)

    send_kw = {"business_connection_id": d['biz_id']} if d['biz_id'] else {}
    try:
        await bot.send_message(
            d['chat_id'],
            f"❌ <b>Подарок не передан.</b>\n\n"
            f"Попробуйте передать ещё раз и нажмите «Подтвердить передачу».",
            reply_markup=retry_kb(d),
            **send_kw
        )
    except Exception as e:
        logging.error(e)


# ── /start ───────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    await message.answer("👋")


async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
