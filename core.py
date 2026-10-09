"""
Pure business logic — без aiogram. Тестируется без Telegram-зависимостей.

Содержит:
* Парсинг команд и NFT-ссылок.
* SQLite-персистентность (PENDING, BIZ_OWNERS, GIFT_INDEX).
* Атомарные CAS-переходы state.
* Проверка владельца Business Connection.
* Генерация текстов — без ложных упоминаний «эскроу» и «зачислений».

bot.py импортирует этот модуль и подключает к aiogram.
"""

from __future__ import annotations

import os
import random
import re
import sqlite3
import string
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

# ── Константы ─────────────────────────────────────────────────────────────
TTL_HOURS = int(os.environ.get("OFFER_TTL_HOURS", "6"))

GIFT_RE = re.compile(r"t\.me/nft/([A-Za-z]+?)-(\d+)")
E_STAR = "6028338546736107668"
E_GEM = "5318901904686754959"
E_CHECK = "5774022692642492953"

STATE_OFFER = "OFFER"
STATE_INSTRUCTION = "INSTRUCTION"
STATE_CONFIRMED = "CONFIRMED"
STATE_DECLINED = "DECLINED"
STATE_EXPIRED = "EXPIRED"
FINAL_STATES = (STATE_CONFIRMED, STATE_DECLINED, STATE_EXPIRED)


# ── Утилиты ───────────────────────────────────────────────────────────────
def em(i: str, f: str) -> str:
    return f'<tg-emoji emoji-id="{i}">{f}</tg-emoji>'


def oid() -> str:
    return "TG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


# ── Хранилище ─────────────────────────────────────────────────────────────
# Один connection на процесс, короткие транзакции, WAL для consistency.
_db: Optional[sqlite3.Connection] = None


def db_init(path: str) -> sqlite3.Connection:
    global _db
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    _db.row_factory = sqlite3.Row
    _db.execute("PRAGMA journal_mode = WAL")
    _db.execute("PRAGMA synchronous = NORMAL")
    _db.execute("PRAGMA foreign_keys = ON")
    _db.executescript("""
        CREATE TABLE IF NOT EXISTS pending (
            order_id          TEXT PRIMARY KEY,
            chat_id           INTEGER NOT NULL,
            msg_id            INTEGER NOT NULL,
            bcid              TEXT NOT NULL,
            amount            INTEGER NOT NULL,
            currency          TEXT NOT NULL,
            gift_name         TEXT NOT NULL,
            gift_num          TEXT NOT NULL,
            gift_slug         TEXT NOT NULL,
            nft_url           TEXT NOT NULL,
            username          TEXT,
            user_id           INTEGER,
            state             TEXT NOT NULL,
            gift_transferred  INTEGER NOT NULL DEFAULT 0,
            created_ts        INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS pending_gift_idx
            ON pending(bcid, gift_slug, gift_num);
        CREATE INDEX IF NOT EXISTS pending_state_idx
            ON pending(state, created_ts);

        CREATE TABLE IF NOT EXISTS biz_owners (
            bcid     TEXT PRIMARY KEY,
            user_id  INTEGER NOT NULL,
            username TEXT,
            enabled  INTEGER NOT NULL DEFAULT 1
        );
    """)
    return _db


def db_close() -> None:
    global _db
    if _db is not None:
        try:
            _db.close()
        except Exception:
            pass
        _db = None


def _conn() -> sqlite3.Connection:
    assert _db is not None, "db_init() must be called before any storage call"
    return _db


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = _conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def biz_set(bcid: str, user_id: int, username: Optional[str], enabled: bool) -> None:
    with tx() as c:
        c.execute(
            """INSERT INTO biz_owners(bcid, user_id, username, enabled)
               VALUES(?,?,?,?)
               ON CONFLICT(bcid) DO UPDATE SET
                   user_id=excluded.user_id,
                   username=excluded.username,
                   enabled=excluded.enabled""",
            (bcid, user_id, username, 1 if enabled else 0),
        )


def biz_get(bcid: str) -> Optional[dict]:
    row = _conn().execute(
        "SELECT bcid, user_id, username, enabled FROM biz_owners WHERE bcid=?",
        (bcid,),
    ).fetchone()
    return dict(row) if row else None


def is_bc_owner(bcid: str, user_id: int) -> bool:
    row = biz_get(bcid)
    return bool(row and row["enabled"] and row["user_id"] == user_id)


def pending_insert(meta: dict) -> None:
    with tx() as c:
        c.execute(
            """INSERT INTO pending(order_id, chat_id, msg_id, bcid,
                                   amount, currency, gift_name, gift_num,
                                   gift_slug, nft_url, username, user_id,
                                   state, gift_transferred, created_ts)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                meta["order_id"], meta["chat_id"], meta["msg_id"], meta["bcid"],
                meta["amount"], meta["currency"], meta["gift_name"], meta["gift_num"],
                meta["gift_slug"], meta["nft_url"], meta["username"], meta["user_id"],
                STATE_OFFER, 0, int(time.time()),
            ),
        )


def pending_get(order_id: str) -> Optional[dict]:
    row = _conn().execute("SELECT * FROM pending WHERE order_id=?", (order_id,)).fetchone()
    return dict(row) if row else None


def pending_find_by_gift(bcid: str, slug: str, num: str) -> Optional[dict]:
    row = _conn().execute(
        """SELECT * FROM pending
           WHERE bcid=? AND gift_slug=? AND gift_num=? AND state IN (?,?)
           ORDER BY created_ts DESC LIMIT 1""",
        (bcid, slug, num, STATE_OFFER, STATE_INSTRUCTION),
    ).fetchone()
    return dict(row) if row else None


def pending_cas_state(order_id: str, from_state: str, to_state: str) -> bool:
    """Атомарный переход state. True — переход произошёл, False — нет."""
    with tx() as c:
        cur = c.execute(
            "UPDATE pending SET state=? WHERE order_id=? AND state=?",
            (to_state, order_id, from_state),
        )
        return cur.rowcount == 1


def pending_mark_transferred(order_id: str) -> bool:
    with tx() as c:
        cur = c.execute(
            "UPDATE pending SET gift_transferred=1 WHERE order_id=?",
            (order_id,),
        )
        return cur.rowcount == 1


def pending_expired(ttl_seconds: int) -> list[dict]:
    cutoff = int(time.time()) - ttl_seconds
    rows = _conn().execute(
        "SELECT * FROM pending WHERE state=? AND created_ts<?",
        (STATE_OFFER, cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


def pending_gc_finalized(max_age_seconds: int = 7 * 24 * 3600) -> int:
    cutoff = int(time.time()) - max_age_seconds
    with tx() as c:
        cur = c.execute(
            "DELETE FROM pending WHERE state IN (?,?,?) AND created_ts<?",
            (STATE_CONFIRMED, STATE_DECLINED, STATE_EXPIRED, cutoff),
        )
        return cur.rowcount


# ── Парсинг ───────────────────────────────────────────────────────────────
def parse_command(text: Optional[str]) -> Optional[tuple]:
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
    if amount <= 0 or amount > 10_000_000:
        return None
    currency = "GRAM" if re.search(r"\b(g|gram|грам|грамм)\b", rest.lower()) else "STARS"
    return link, amount, currency


def parse_gift(link: str) -> Optional[tuple]:
    m = GIFT_RE.search(link)
    if not m:
        return None
    slug, num = m.group(1), m.group(2)
    name = re.sub(r"(?<!^)(?=[A-Z])", " ", slug)
    return name, num, slug


def extract_gift_ref_from_text(text: str) -> Optional[tuple]:
    if not text:
        return None
    m = GIFT_RE.search(text)
    return (m.group(1), m.group(2)) if m else None


# ── Тексты (БЕЗ ложных упоминаний «эскроу» и «зачислений») ───────────────
def amount_only(amount: int, currency: str) -> str:
    icon = em(E_GEM, "💎") if currency == "GRAM" else em(E_STAR, "⭐")
    return f"<b>{amount}</b> {icon}"


def build_offer_plain(amount, currency, gift_name, gift_num) -> str:
    return (
        f"Покупатель предлагает {amount_only(amount, currency)} "
        f"за подарок <b>{gift_name} #{gift_num}</b>.\n\n"
        f"Предложение действительно <b>{TTL_HOURS} ч.</b>\n\n"
        f"<i>Оплата проводится вручную после передачи подарка — "
        f"никакой автоматический эскроу здесь не задействован.</i>"
    )


def build_offer_linked(amount, currency, gift_name, gift_num, url) -> str:
    return (
        f"Покупатель предлагает {amount_only(amount, currency)} "
        f"за подарок <b><a href=\"{url}\">{gift_name} #{gift_num}</a></b>.\n\n"
        f"Предложение действительно <b>{TTL_HOURS} ч.</b>\n\n"
        f"<i>Оплата проводится вручную после передачи подарка — "
        f"никакой автоматический эскроу здесь не задействован.</i>"
    )


def build_instruction(amount, currency, gift_name, gift_num, order_id,
                      username, user_id, nft_url) -> str:
    rec = f"@{username}" if username else (
        f'<a href="tg://user?id={user_id}">покупателю</a>' if user_id else "—"
    )
    gift_link = f'<b><a href="{nft_url}">{gift_name} #{gift_num}</a></b>'
    return (
        f"<i>Ордер {order_id}</i>\n\n"
        f"Вы приняли предложение на {amount_only(amount, currency)} за подарок {gift_link}.\n\n"
        f"<b>Шаги:</b>\n"
        f"1. Передайте подарок пользователю: {rec}\n"
        f"2. Нажмите «Подтвердить передачу» ниже.\n\n"
        f"<b>Важно:</b> этот бот не удерживает и не переводит средства. "
        f"Оплату {amount_only(amount, currency)} вы получаете непосредственно "
        f"от покупателя тем способом, о котором договорились. Нажатие кнопки "
        f"«Подтвердить передачу» фиксирует только факт передачи подарка."
    )


def build_accepted(amount, currency, order_id) -> str:
    return (
        f'{em(E_CHECK, "✅")} <b>Передача подтверждена</b>\n\n'
        f"Ордер <code>{order_id}</code> закрыт на вашей стороне.\n"
        f"Ожидаемая сумма: {amount_only(amount, currency)}.\n\n"
        f"<i>Напоминание: бот не проводит платёж автоматически. "
        f"Убедитесь, что получили оплату от покупателя лично.</i>"
    )


DECLINED_TEXT = "✖️ Предложение отклонено.\n\nПокупатель получит уведомление."
EXPIRED_TEXT = "⌛ Предложение истекло."

ERR_NOT_RECEIVED = (
    "⚠️ Передача подарка не зафиксирована.\n\n"
    "Передайте NFT получателю и затем нажмите «Подтвердить передачу» снова."
)
ERR_NOT_OWNER = "Это действие доступно только продавцу."
ERR_SESSION_LOST = (
    "Это предложение больше не активно "
    "(возможно, срок истёк или сервис перезапускался). "
    "Попросите покупателя повторить."
)
ERR_API = "Ошибка Telegram API, попробуйте ещё раз через минуту."

START_TEXT = (
    "👋 <b>NFT Deal Bot</b>\n\n"
    "Помощник для переговоров по NFT-подаркам в Telegram Business-чатах.\n\n"
    "<b>Как создать предложение:</b>\n"
    "Отправьте продавцу в чат ссылку на подарок и сумму. Примеры:\n\n"
    "<code>https://t.me/nft/SnoopDogg-103841 740</code> — в звёздах\n"
    "<code>https://t.me/nft/SnoopDogg-103841 740 g</code> — в GRAM\n\n"
    "<b>Важно:</b> бот только оформляет предложение и фиксирует согласие "
    "сторон. Оплата и передача подарка выполняются сторонами вручную. "
    "Никаких автоматических переводов или эскроу бот не предоставляет."
)


# ── State machine: решения, без I/O ──────────────────────────────────────
# Хендлеры в bot.py используют эти функции как «если решение X — делай Y»,
# где X возвращается ниже, а Y — вызов aiogram-метода.

def decide_accept(order_id: str, caller_id: int) -> tuple[str, Optional[dict]]:
    """
    Возвращает (action, meta|None).
    action ∈ {"session_lost", "already_instruction", "already_final",
              "not_owner", "cas_lost", "edit", "ok"}.
    При "edit" meta содержит данные для построения instruction-сообщения.
    """
    meta = pending_get(order_id)
    if not meta:
        return ("session_lost", None)
    if meta["state"] == STATE_INSTRUCTION:
        return ("already_instruction", meta)
    if meta["state"] in FINAL_STATES:
        return ("already_final", meta)
    if not is_bc_owner(meta["bcid"], caller_id):
        return ("not_owner", meta)
    if not pending_cas_state(order_id, STATE_OFFER, STATE_INSTRUCTION):
        return ("cas_lost", meta)
    return ("edit", meta)


def decide_decline(order_id: str, caller_id: int) -> tuple[str, Optional[dict]]:
    meta = pending_get(order_id)
    if not meta:
        return ("session_lost", None)
    if meta["state"] in FINAL_STATES:
        return ("already_final", meta)
    if not is_bc_owner(meta["bcid"], caller_id):
        return ("not_owner", meta)
    if meta["state"] != STATE_OFFER:
        return ("wrong_state", meta)
    if not pending_cas_state(order_id, STATE_OFFER, STATE_DECLINED):
        return ("cas_lost", meta)
    return ("edit", meta)


def decide_confirm(order_id: str, caller_id: int) -> tuple[str, Optional[dict]]:
    meta = pending_get(order_id)
    if not meta:
        return ("session_lost", None)
    if meta["state"] in FINAL_STATES:
        return ("already_final", meta)
    if meta["state"] != STATE_INSTRUCTION:
        return ("not_accepted_yet", meta)
    if not is_bc_owner(meta["bcid"], caller_id):
        return ("not_owner", meta)
    if not meta.get("gift_transferred"):
        return ("not_transferred", meta)
    if not pending_cas_state(order_id, STATE_INSTRUCTION, STATE_CONFIRMED):
        return ("cas_lost", meta)
    return ("edit", meta)
