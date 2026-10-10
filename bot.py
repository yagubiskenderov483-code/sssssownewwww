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


def kb_instruction(order_id, buyer_username, buyer_user_id) -> InlineKeyboardMarkup:
    if buyer_username:
        send_url = f"tg://resolve?domain={buyer_username}"
    elif buyer_user_id:
        send_url = f"tg://user?id={buyer_user_id}"
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
    log.info("biz_conn %s id=%s user_id=%s username=@%s",
             "ON" if bc.is_enabled else "OFF", bc.id, bc.user.id, bc.user.username)


# ── Business message ─────────────────────────────────────────────────────
def _extract_gift_ref(message: Message):
    """Пробуем вытащить ссылку на подарок из структурированных полей или текста.

    ВАЖНО: эта функция может ничего не найти в случае передачи unique_gift
    через нативный Telegram UI — там прилетает service message без текста
    с nft-ссылкой. Поэтому `gift_transferred` в core НЕ используется как
    блокировка подтверждения передачи; это лишь мягкий индикатор.
    """
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


async def _ensure_biz_owner(bcid: str) -> dict | None:
    """Гарантируем, что для bcid есть запись в biz_owners.

    1) Если есть enabled → возвращаем.
    2) Иначе пробуем get_business_connection через API.
    3) Если и это не удалось — None, caller решает что делать.

    Эта функция КРИТИЧНА после рестарта: business_connection events
    Telegram НЕ переигрывает, поэтому единственный путь восстановить
    запись — явный API-fetch по bcid.
    """
    info = C.biz_get(bcid)
    if info and info["enabled"]:
        return info
    try:
        bc = await bot.get_business_connection(bcid)
    except Exception as e:
        log.error("get_business_connection bcid=%s: %s", bcid, e)
        return None
    C.biz_set(bcid, bc.user.id, bc.user.username, bool(bc.is_enabled))
    log.info("biz_owner recovered via API id=%s user_id=%s", bcid, bc.user.id)
    return C.biz_get(bcid)


@dp.business_message(F.text)
async def handle_business_message(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return
    log.info("business_message chat=%s from=%s bcid=%s text=%r",
             message.chat.id, message.from_user.id if message.from_user else None,
             bcid, (message.text or "")[:80])

    # Если это сообщение фиксирует факт передачи ранее принятого оффера —
    # помечаем gift_transferred=1 (это только для информации, не блокирует
    # подтверждение).
    ref = _extract_gift_ref(message)
    if ref:
        slug, num = ref
        pending = C.pending_find_by_gift(bcid, slug, num)
        if pending and pending["state"] == C.STATE_INSTRUCTION:
            C.pending_mark_transferred(pending["order_id"])
            log.info("gift_transferred (soft) order=%s", pending["order_id"])
            # Не return — вдруг buyer в одном сообщении и подарок передал,
            # и новый оффер прислал. Хотя на практике маловероятно.

    parsed = C.parse_command(message.text or "")
    if not parsed:
        return
    link, amount, currency = parsed
    gift = C.parse_gift(link)
    if not gift:
        return
    gift_name, gift_num, slug = gift

    seller_info = await _ensure_biz_owner(bcid)
    if not seller_info:
        return
    seller_user_id = seller_info["user_id"]
    seller_username = seller_info["username"]

    # Покупатель — автор сообщения.
    buyer = message.from_user
    buyer_user_id = buyer.id if buyer else None
    buyer_username = buyer.username if buyer else None

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
        "seller_user_id": seller_user_id,
        "seller_username": seller_username,
        "buyer_user_id": buyer_user_id,
        "buyer_username": buyer_username,
    })
    log.info("offer_created order=%s seller=%s buyer=%s gift=%s-%s amount=%s",
             order_id, seller_user_id, buyer_user_id, slug, gift_num, amount)

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
async def _safe_answer(cb: CallbackQuery, text: str = "", show_alert: bool = False):
    """cb.answer с подавлением исключений — чтобы ветка exit никогда не падала."""
    try:
        await cb.answer(text, show_alert=show_alert)
    except Exception as e:
        log.warning("cb.answer failed id=%s: %s", cb.id, e)


@dp.callback_query(F.data.startswith("decline:"))
async def on_decline(cb: CallbackQuery):
    data = cb.data or ""
    order_id = data.split(":", 1)[1] if ":" in data else ""
    log.info("cb decline order=%s from=%s", order_id, cb.from_user.id)
    try:
        action, meta = C.decide_decline(order_id, cb.from_user.id)
        log.info("decide_decline order=%s action=%s", order_id, action)
        if action == "session_lost":
            return await _safe_answer(cb, C.ERR_SESSION_LOST, show_alert=True)
        if action == "already_final":
            return await _safe_answer(cb, "Предложение уже закрыто.")
        if action == "not_owner":
            return await _safe_answer(cb, C.ERR_NOT_OWNER, show_alert=True)
        if action == "wrong_state":
            return await _safe_answer(cb, "Нельзя отклонить уже принятое.", show_alert=True)
        if action == "cas_lost":
            return await _safe_answer(cb, "Состояние изменилось.")
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
            return await _safe_answer(cb, C.ERR_API, show_alert=True)
        await _safe_answer(cb, "Отклонено.")
    except Exception:
        log.exception("on_decline crashed order=%s", order_id)
        await _safe_answer(cb, C.ERR_API, show_alert=True)


@dp.callback_query(F.data.startswith("accept:"))
async def on_accept(cb: CallbackQuery):
    data = cb.data or ""
    order_id = data.split(":", 1)[1] if ":" in data else ""
    log.info("cb accept order=%s from=%s", order_id, cb.from_user.id)
    try:
        # На всякий случай — восстановить biz_owners из API, если пусто.
        # Это НЕ блокирует: is_order_seller читает snapshot из pending.
        meta_preview = C.pending_get(order_id)
        if meta_preview:
            await _ensure_biz_owner(meta_preview["bcid"])

        action, meta = C.decide_accept(order_id, cb.from_user.id)
        log.info("decide_accept order=%s action=%s", order_id, action)
        if action == "session_lost":
            return await _safe_answer(cb, C.ERR_SESSION_LOST, show_alert=True)
        if action == "already_instruction":
            return await _safe_answer(cb, "Предложение уже принято.")
        if action == "already_final":
            return await _safe_answer(cb, "Предложение уже закрыто.")
        if action == "not_owner":
            log.warning("not_owner order=%s from=%s seller_in_db=%s",
                        order_id, cb.from_user.id,
                        meta.get("seller_user_id") if meta else None)
            return await _safe_answer(cb, C.ERR_NOT_OWNER, show_alert=True)
        if action == "cas_lost":
            return await _safe_answer(cb, "Состояние изменилось, обновите чат.")
        # action == "edit"
        try:
            await bot.edit_message_text(
                chat_id=meta["chat_id"], message_id=meta["msg_id"],
                text=C.build_instruction(
                    meta["amount"], meta["currency"],
                    meta["gift_name"], meta["gift_num"], order_id,
                    meta.get("buyer_username"), meta.get("buyer_user_id"),
                    meta["nft_url"],
                ),
                reply_markup=kb_instruction(
                    order_id,
                    meta.get("buyer_username"),
                    meta.get("buyer_user_id"),
                ),
                business_connection_id=meta["bcid"],
                link_preview_options=LinkPreviewOptions(
                    is_disabled=False, prefer_large_media=True, show_above_text=True,
                ),
            )
        except TelegramBadRequest as e:
            # Откат state, иначе БД и UI разойдутся
            C.pending_cas_state(order_id, C.STATE_INSTRUCTION, C.STATE_OFFER)
            log.error("accept_edit order=%s: %s", order_id, e)
            return await _safe_answer(cb, C.ERR_API, show_alert=True)
        await _safe_answer(cb, "Принято.")
    except Exception:
        log.exception("on_accept crashed order=%s", order_id)
        await _safe_answer(cb, C.ERR_API, show_alert=True)


@dp.callback_query(F.data.startswith("confirm:"))
async def on_confirm(cb: CallbackQuery):
    data = cb.data or ""
    order_id = data.split(":", 1)[1] if ":" in data else ""
    log.info("cb confirm order=%s from=%s", order_id, cb.from_user.id)
    try:
        action, meta = C.decide_confirm(order_id, cb.from_user.id)
        log.info("decide_confirm order=%s action=%s", order_id, action)
        if action == "session_lost":
            return await _safe_answer(cb, C.ERR_SESSION_LOST, show_alert=True)
        if action == "already_final":
            return await _safe_answer(cb, "Предложение уже закрыто.")
        if action == "not_accepted_yet":
            return await _safe_answer(cb, "Сначала примите предложение.", show_alert=True)
        if action == "not_owner":
            return await _safe_answer(cb, C.ERR_NOT_OWNER, show_alert=True)
        if action == "cas_lost":
            return await _safe_answer(cb, "Состояние изменилось, обновите чат.")
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
            return await _safe_answer(cb, C.ERR_API, show_alert=True)
        await _safe_answer(cb, "Подтверждено.")
    except Exception:
        log.exception("on_confirm crashed order=%s", order_id)
        await _safe_answer(cb, C.ERR_API, show_alert=True)


# Fallback — ловим любой нераспознанный callback_query, чтобы спиннер
# у пользователя не висел бесконечно.
@dp.callback_query()
async def on_unknown_callback(cb: CallbackQuery):
    log.warning("callback not matched: data=%r from=%s", cb.data, cb.from_user.id)
    await _safe_answer(cb, "Неизвестная команда — обновите чат.", show_alert=True)


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


async def recover_biz_owners_from_pending():
    """На старте пробуем для каждого bcid активных офферов восстановить
    запись в biz_owners через API. Нужно потому что business_connection
    events не переигрываются после рестарта."""
    bcids = C.pending_distinct_bcids()
    if not bcids:
        return
    log.info("recovering biz_owners for %d bcid(s) from active pending", len(bcids))
    for bcid in bcids:
        try:
            bc = await bot.get_business_connection(bcid)
            C.biz_set(bcid, bc.user.id, bc.user.username, bool(bc.is_enabled))
            log.info("  recovered bcid=%s user_id=%s enabled=%s",
                     bcid, bc.user.id, bc.is_enabled)
        except Exception as e:
            log.warning("  could not recover bcid=%s: %s", bcid, e)


# ── main ─────────────────────────────────────────────────────────────────
async def main():
    log.info("starting; DB_PATH=%s TTL_HOURS=%s", DB_PATH, C.TTL_HOURS)
    C.db_init(DB_PATH)
    try:
        me = await bot.get_me()
        log.info("bot: @%s id=%s", me.username, me.id)
    except Exception as e:
        log.error("get_me failed: %s", e)
    await recover_biz_owners_from_pending()

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
