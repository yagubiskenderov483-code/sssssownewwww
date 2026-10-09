"""
Тесты для core.py — всей бизнес-логики без Telegram.

Запуск:
    pytest -v test_core.py
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import core as C


# ── fixtures ──────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Своя БД на каждый тест."""
    C.db_init(str(tmp_path / "t.db"))
    yield
    C.db_close()


def seed_offer(order_id="TG-TEST01", bcid="bc1", owner_id=200,
               state=C.STATE_OFFER, transferred=False):
    C.biz_set(bcid, owner_id, "seller", True)
    C.pending_insert({
        "order_id": order_id, "chat_id": 1001, "msg_id": 42, "bcid": bcid,
        "amount": 500, "currency": "STARS",
        "gift_name": "SnoopDogg", "gift_num": "1", "gift_slug": "SnoopDogg",
        "nft_url": "https://t.me/nft/SnoopDogg-1",
        "username": "buyer", "user_id": 999,
    })
    if state != C.STATE_OFFER:
        C.pending_cas_state(order_id, C.STATE_OFFER, state)
    if transferred:
        C.pending_mark_transferred(order_id)


# ── Парсинг ───────────────────────────────────────────────────────────────
class TestParsing:
    def test_valid_stars(self):
        assert C.parse_command("https://t.me/nft/Foo-123 500") == (
            "https://t.me/nft/Foo-123", 500, "STARS",
        )

    def test_without_scheme(self):
        link, _, _ = C.parse_command("t.me/nft/Foo-123 500")
        assert link == "https://t.me/nft/Foo-123"

    def test_gram_english(self):
        _, amt, cur = C.parse_command("t.me/nft/Foo-123 500 g")
        assert amt == 500 and cur == "GRAM"

    def test_gram_russian(self):
        _, _, cur = C.parse_command("t.me/nft/Foo-123 500 грамм")
        assert cur == "GRAM"

    def test_no_amount(self):
        assert C.parse_command("t.me/nft/Foo-123") is None

    def test_no_link(self):
        assert C.parse_command("500 stars") is None

    def test_zero_and_huge_rejected(self):
        assert C.parse_command("t.me/nft/Foo-123 0") is None
        assert C.parse_command("t.me/nft/Foo-123 99999999") is None

    def test_empty(self):
        assert C.parse_command("") is None
        assert C.parse_command(None) is None

    def test_parse_gift(self):
        assert C.parse_gift("https://t.me/nft/SnoopDogg-42") == ("Snoop Dogg", "42", "SnoopDogg")

    def test_parse_gift_bad(self):
        assert C.parse_gift("https://example.com/foo") is None


# ── SQLite CAS / recovery ─────────────────────────────────────────────────
class TestStorage:
    def test_insert_and_get(self):
        seed_offer()
        meta = C.pending_get("TG-TEST01")
        assert meta is not None
        assert meta["state"] == C.STATE_OFFER
        assert meta["amount"] == 500
        assert meta["gift_transferred"] == 0

    def test_cas_only_once(self):
        seed_offer()
        assert C.pending_cas_state("TG-TEST01", C.STATE_OFFER, C.STATE_INSTRUCTION) is True
        assert C.pending_cas_state("TG-TEST01", C.STATE_OFFER, C.STATE_INSTRUCTION) is False
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_INSTRUCTION

    def test_cas_wrong_from(self):
        seed_offer()
        # Из INSTRUCTION не выйдет — пока state=OFFER
        assert C.pending_cas_state("TG-TEST01", C.STATE_INSTRUCTION, C.STATE_CONFIRMED) is False
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_OFFER

    def test_cas_nonexistent_order(self):
        assert C.pending_cas_state("NOPE", C.STATE_OFFER, C.STATE_CONFIRMED) is False

    def test_find_by_gift(self):
        seed_offer()
        found = C.pending_find_by_gift("bc1", "SnoopDogg", "1")
        assert found and found["order_id"] == "TG-TEST01"
        # Чужой bcid — нет
        assert C.pending_find_by_gift("bc2", "SnoopDogg", "1") is None
        # Чужой подарок — нет
        assert C.pending_find_by_gift("bc1", "Other", "1") is None

    def test_find_by_gift_only_live_states(self):
        seed_offer(state=C.STATE_DECLINED)
        assert C.pending_find_by_gift("bc1", "SnoopDogg", "1") is None

    def test_mark_transferred(self):
        seed_offer()
        assert C.pending_mark_transferred("TG-TEST01") is True
        assert C.pending_get("TG-TEST01")["gift_transferred"] == 1

    def test_biz_owner_check(self):
        C.biz_set("bc9", 42, "alice", True)
        assert C.is_bc_owner("bc9", 42) is True
        assert C.is_bc_owner("bc9", 43) is False
        # Отключение BC
        C.biz_set("bc9", 42, "alice", False)
        assert C.is_bc_owner("bc9", 42) is False
        # Несуществующий bcid
        assert C.is_bc_owner("nope", 42) is False

    def test_expired_only_offers(self):
        seed_offer(order_id="A")
        seed_offer(order_id="B", state=C.STATE_INSTRUCTION)
        C._db.execute("UPDATE pending SET created_ts=0")
        exp = C.pending_expired(3600)
        order_ids = {m["order_id"] for m in exp}
        assert order_ids == {"A"}

    def test_restart_recovery(self, tmp_path):
        """ГЛАВНЫЙ БАГ: PENDING переживает рестарт процесса."""
        path = str(tmp_path / "restart.db")
        C.db_close()
        C.db_init(path)
        seed_offer(order_id="TG-RESTART")
        C.db_close()
        # Второй процесс
        C.db_init(path)
        meta = C.pending_get("TG-RESTART")
        assert meta is not None
        assert meta["state"] == C.STATE_OFFER

    def test_gc_finalized(self):
        seed_offer(order_id="A", state=C.STATE_CONFIRMED)
        seed_offer(order_id="B")  # OFFER — не должен подчиститься
        C._db.execute("UPDATE pending SET created_ts=0")
        deleted = C.pending_gc_finalized(max_age_seconds=1)
        assert deleted == 1
        assert C.pending_get("A") is None
        assert C.pending_get("B") is not None


# ── decide_accept ─────────────────────────────────────────────────────────
class TestDecideAccept:
    def test_session_lost(self):
        action, meta = C.decide_accept("NOPE", 200)
        assert action == "session_lost"
        assert meta is None

    def test_not_owner(self):
        seed_offer(owner_id=200)
        action, _ = C.decide_accept("TG-TEST01", 999)
        assert action == "not_owner"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_OFFER

    def test_happy_path(self):
        seed_offer(owner_id=200)
        action, meta = C.decide_accept("TG-TEST01", 200)
        assert action == "edit"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_INSTRUCTION

    def test_double_accept(self):
        seed_offer(owner_id=200)
        a1, _ = C.decide_accept("TG-TEST01", 200)
        a2, _ = C.decide_accept("TG-TEST01", 200)
        assert a1 == "edit"
        assert a2 == "already_instruction"

    def test_accept_already_final(self):
        seed_offer(owner_id=200, state=C.STATE_DECLINED)
        action, _ = C.decide_accept("TG-TEST01", 200)
        assert action == "already_final"

    def test_concurrent_cas_from_separate_connections(self, tmp_path):
        """
        Два НЕЗАВИСИМЫХ SQLite-соединения к одной БД делают CAS одновременно.
        Это реалистичная модель двух процессов бота или двух event-loop
        iterations: WAL + BEGIN IMMEDIATE гарантирует, что лишь один
        UPDATE затронет строку.
        """
        import sqlite3
        path = str(tmp_path / "race.db")
        C.db_close()
        C.db_init(path)
        seed_offer(owner_id=200)
        C.db_close()

        # Открываем две независимые connection к одной базе
        def one_cas():
            conn = sqlite3.connect(path, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE pending SET state=? WHERE order_id=? AND state=?",
                (C.STATE_INSTRUCTION, "TG-TEST01", C.STATE_OFFER),
            )
            rows = cur.rowcount
            conn.execute("COMMIT")
            conn.close()
            return rows

        r1 = one_cas()
        r2 = one_cas()
        # Ровно один UPDATE затронул строку, второй увидел уже-INSTRUCTION
        assert sorted([r1, r2]) == [0, 1]

        # А третьему clean connection покажет итоговое состояние
        C.db_init(path)
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_INSTRUCTION


# ── decide_decline ────────────────────────────────────────────────────────
class TestDecideDecline:
    def test_happy_path(self):
        seed_offer(owner_id=200)
        action, _ = C.decide_decline("TG-TEST01", 200)
        assert action == "edit"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_DECLINED

    def test_decline_after_accept(self):
        seed_offer(owner_id=200, state=C.STATE_INSTRUCTION)
        action, _ = C.decide_decline("TG-TEST01", 200)
        assert action == "wrong_state"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_INSTRUCTION

    def test_decline_not_owner(self):
        seed_offer(owner_id=200)
        action, _ = C.decide_decline("TG-TEST01", 999)
        assert action == "not_owner"


# ── decide_confirm ────────────────────────────────────────────────────────
class TestDecideConfirm:
    def test_without_accept(self):
        seed_offer(owner_id=200)
        action, _ = C.decide_confirm("TG-TEST01", 200)
        assert action == "not_accepted_yet"

    def test_without_transfer(self):
        seed_offer(owner_id=200, state=C.STATE_INSTRUCTION, transferred=False)
        action, _ = C.decide_confirm("TG-TEST01", 200)
        assert action == "not_transferred"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_INSTRUCTION

    def test_happy_path(self):
        seed_offer(owner_id=200, state=C.STATE_INSTRUCTION, transferred=True)
        action, _ = C.decide_confirm("TG-TEST01", 200)
        assert action == "edit"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_CONFIRMED

    def test_not_owner(self):
        seed_offer(owner_id=200, state=C.STATE_INSTRUCTION, transferred=True)
        action, _ = C.decide_confirm("TG-TEST01", 999)
        assert action == "not_owner"

    def test_already_final(self):
        seed_offer(owner_id=200, state=C.STATE_CONFIRMED, transferred=True)
        action, _ = C.decide_confirm("TG-TEST01", 200)
        assert action == "already_final"


# ── Отсутствие ложных «эскроу»/«зачислений» в текстах ────────────────────
class TestNoFraudTexts:
    def test_no_zachisleno_in_instruction(self):
        txt = C.build_instruction(100, "STARS", "Foo", "1",
                                  "TG-XX", "u", 1, "http://x")
        assert "зачислен" not in txt.lower()
        assert "эскроу-систем" not in txt.lower()
        assert "не удерживает" in txt.lower()

    def test_no_zachisleno_in_accepted(self):
        txt = C.build_accepted(100, "STARS", "TG-XX")
        assert "зачислен" not in txt.lower()
        assert "не проводит платёж автоматически" in txt.lower()

    def test_offer_mentions_escrow_only_to_deny_it(self):
        txt = C.build_offer_plain(100, "STARS", "Foo", "1")
        # Единственное упоминание — опровержение
        assert "никакой автоматический эскроу" in txt.lower()

    def test_start_disclaims_escrow(self):
        assert "никаких автоматических переводов или эскроу" in C.START_TEXT.lower()


# ── Полный сценарий сделки ────────────────────────────────────────────────
class TestFullScenario:
    def test_offer_accept_transfer_confirm(self):
        """Полный жизненный цикл сделки — от OFFER до CONFIRMED."""
        seed_offer(owner_id=200)
        # Продавец принимает
        a, _ = C.decide_accept("TG-TEST01", 200)
        assert a == "edit"
        # Confirm без transferred — отказ
        a, _ = C.decide_confirm("TG-TEST01", 200)
        assert a == "not_transferred"
        # Покупатель передаёт подарок — сигнал через business_message
        assert C.pending_mark_transferred("TG-TEST01")
        # Теперь confirm проходит
        a, _ = C.decide_confirm("TG-TEST01", 200)
        assert a == "edit"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_CONFIRMED

    def test_offer_decline(self):
        seed_offer(owner_id=200)
        a, _ = C.decide_decline("TG-TEST01", 200)
        assert a == "edit"
        # Повторный клик — уже финальный
        a, _ = C.decide_decline("TG-TEST01", 200)
        assert a == "already_final"
