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


def seed_offer(order_id="TG-TEST01", bcid="bc1", seller_id=200,
               buyer_id=999, state=C.STATE_OFFER, transferred=False):
    C.biz_set(bcid, seller_id, "seller", True)
    C.pending_insert({
        "order_id": order_id, "chat_id": 1001, "msg_id": 42, "bcid": bcid,
        "amount": 500, "currency": "STARS",
        "gift_name": "SnoopDogg", "gift_num": "1", "gift_slug": "SnoopDogg",
        "nft_url": "https://t.me/nft/SnoopDogg-1",
        "seller_user_id": seller_id, "seller_username": "seller",
        "buyer_user_id": buyer_id, "buyer_username": "buyer",
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
        assert meta["seller_user_id"] == 200
        assert meta["buyer_user_id"] == 999

    def test_cas_only_once(self):
        seed_offer()
        assert C.pending_cas_state("TG-TEST01", C.STATE_OFFER, C.STATE_INSTRUCTION) is True
        assert C.pending_cas_state("TG-TEST01", C.STATE_OFFER, C.STATE_INSTRUCTION) is False
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_INSTRUCTION

    def test_cas_wrong_from(self):
        seed_offer()
        assert C.pending_cas_state("TG-TEST01", C.STATE_INSTRUCTION, C.STATE_CONFIRMED) is False
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_OFFER

    def test_cas_nonexistent_order(self):
        assert C.pending_cas_state("NOPE", C.STATE_OFFER, C.STATE_CONFIRMED) is False

    def test_find_by_gift(self):
        seed_offer()
        found = C.pending_find_by_gift("bc1", "SnoopDogg", "1")
        assert found and found["order_id"] == "TG-TEST01"
        assert C.pending_find_by_gift("bc2", "SnoopDogg", "1") is None
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
        C.biz_set("bc9", 42, "alice", False)
        assert C.is_bc_owner("bc9", 42) is False
        assert C.is_bc_owner("nope", 42) is False

    def test_is_order_seller_independent_of_biz_owners(self):
        """КРИТИЧНО: проверка владельца сделки работает даже если
        biz_owners таблица пуста (эмуляция рестарта без переигрывания
        business_connection events)."""
        seed_offer(seller_id=200)
        # Чистим biz_owners
        C._db.execute("DELETE FROM biz_owners")
        # biz-check ломается
        assert C.is_bc_owner("bc1", 200) is False
        # но order-seller check продолжает работать — snapshot в pending
        assert C.is_order_seller("TG-TEST01", 200) is True
        assert C.is_order_seller("TG-TEST01", 999) is False

    def test_expired_only_offers(self):
        seed_offer(order_id="A")
        seed_offer(order_id="B", state=C.STATE_INSTRUCTION)
        C._db.execute("UPDATE pending SET created_ts=0")
        exp = C.pending_expired(3600)
        order_ids = {m["order_id"] for m in exp}
        assert order_ids == {"A"}

    def test_restart_recovery(self, tmp_path):
        """После рестарта PENDING + snapshot seller_id переживают."""
        path = str(tmp_path / "restart.db")
        C.db_close()
        C.db_init(path)
        seed_offer(order_id="TG-RESTART")
        C.db_close()
        C.db_init(path)
        meta = C.pending_get("TG-RESTART")
        assert meta is not None
        assert meta["state"] == C.STATE_OFFER
        assert meta["seller_user_id"] == 200
        # И проверка владельца всё ещё работает:
        assert C.is_order_seller("TG-RESTART", 200) is True

    def test_gc_finalized(self):
        seed_offer(order_id="A", state=C.STATE_CONFIRMED)
        seed_offer(order_id="B")
        C._db.execute("UPDATE pending SET created_ts=0")
        deleted = C.pending_gc_finalized(max_age_seconds=1)
        assert deleted == 1
        assert C.pending_get("A") is None
        assert C.pending_get("B") is not None

    def test_distinct_bcids(self):
        seed_offer(order_id="A", bcid="bcA")
        seed_offer(order_id="B", bcid="bcB")
        seed_offer(order_id="C", bcid="bcA", state=C.STATE_DECLINED)
        bcids = sorted(C.pending_distinct_bcids())
        assert bcids == ["bcA", "bcB"]

    def test_schema_migration_from_v1(self, tmp_path):
        """Старая v1 схема без seller_user_id не должна ломаться."""
        import sqlite3
        path = str(tmp_path / "v1.db")
        C.db_close()
        # Создаём старую схему вручную
        c = sqlite3.connect(path, isolation_level=None)
        c.execute("""CREATE TABLE pending (
            order_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL,
            msg_id INTEGER NOT NULL, bcid TEXT NOT NULL,
            amount INTEGER NOT NULL, currency TEXT NOT NULL,
            gift_name TEXT NOT NULL, gift_num TEXT NOT NULL,
            gift_slug TEXT NOT NULL, nft_url TEXT NOT NULL,
            username TEXT, user_id INTEGER,
            state TEXT NOT NULL,
            gift_transferred INTEGER NOT NULL DEFAULT 0,
            created_ts INTEGER NOT NULL
        )""")
        c.execute("""INSERT INTO pending VALUES(
            'OLD-1', 100, 42, 'bc1', 500, 'STARS',
            'Snoop', '1', 'SnoopDogg', 'https://x',
            'seller_u', 200,
            'OFFER', 0, 123
        )""")
        c.close()
        # Открываем через db_init — должна мигрировать
        C.db_init(path)
        meta = C.pending_get("OLD-1")
        assert meta is not None
        assert meta["seller_user_id"] == 200
        assert meta["seller_username"] == "seller_u"
        # И проверка владельца работает по мигрированной записи
        assert C.is_order_seller("OLD-1", 200) is True


# ── decide_accept ─────────────────────────────────────────────────────────
class TestDecideAccept:
    def test_session_lost(self):
        action, meta = C.decide_accept("NOPE", 200)
        assert action == "session_lost"
        assert meta is None

    def test_not_owner(self):
        seed_offer(seller_id=200)
        action, _ = C.decide_accept("TG-TEST01", 999)  # buyer, не seller
        assert action == "not_owner"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_OFFER

    def test_happy_path(self):
        seed_offer(seller_id=200)
        action, meta = C.decide_accept("TG-TEST01", 200)
        assert action == "edit"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_INSTRUCTION

    def test_accept_after_restart_with_empty_biz_owners(self):
        """КРИТИЧНО: после рестарта biz_owners пуст, но accept от seller
        должен работать благодаря snapshot в pending."""
        seed_offer(seller_id=200)
        C._db.execute("DELETE FROM biz_owners")
        action, _ = C.decide_accept("TG-TEST01", 200)
        assert action == "edit"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_INSTRUCTION

    def test_double_accept(self):
        seed_offer(seller_id=200)
        a1, _ = C.decide_accept("TG-TEST01", 200)
        a2, _ = C.decide_accept("TG-TEST01", 200)
        assert a1 == "edit"
        assert a2 == "already_instruction"

    def test_accept_already_final(self):
        seed_offer(seller_id=200, state=C.STATE_DECLINED)
        action, _ = C.decide_accept("TG-TEST01", 200)
        assert action == "already_final"

    def test_concurrent_cas_from_separate_connections(self, tmp_path):
        import sqlite3
        path = str(tmp_path / "race.db")
        C.db_close()
        C.db_init(path)
        seed_offer(seller_id=200)
        C.db_close()

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
        assert sorted([r1, r2]) == [0, 1]
        C.db_init(path)
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_INSTRUCTION


# ── decide_decline ────────────────────────────────────────────────────────
class TestDecideDecline:
    def test_happy_path(self):
        seed_offer(seller_id=200)
        action, _ = C.decide_decline("TG-TEST01", 200)
        assert action == "edit"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_DECLINED

    def test_decline_after_accept(self):
        seed_offer(seller_id=200, state=C.STATE_INSTRUCTION)
        action, _ = C.decide_decline("TG-TEST01", 200)
        assert action == "wrong_state"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_INSTRUCTION

    def test_decline_not_owner(self):
        seed_offer(seller_id=200)
        action, _ = C.decide_decline("TG-TEST01", 999)
        assert action == "not_owner"


# ── decide_confirm (БЕЗ требования gift_transferred) ─────────────────────
class TestDecideConfirm:
    def test_without_accept(self):
        seed_offer(seller_id=200)
        action, _ = C.decide_confirm("TG-TEST01", 200)
        assert action == "not_accepted_yet"

    def test_confirm_without_transfer_now_allowed(self):
        """КРИТИЧНО: передача unique_gift через Telegram UI генерирует
        service message без текста, автодетекция gift_transferred не
        срабатывает. Confirm НЕ должен требовать gift_transferred=1."""
        seed_offer(seller_id=200, state=C.STATE_INSTRUCTION, transferred=False)
        action, _ = C.decide_confirm("TG-TEST01", 200)
        assert action == "edit"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_CONFIRMED

    def test_happy_path_with_transfer(self):
        seed_offer(seller_id=200, state=C.STATE_INSTRUCTION, transferred=True)
        action, _ = C.decide_confirm("TG-TEST01", 200)
        assert action == "edit"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_CONFIRMED

    def test_not_owner(self):
        seed_offer(seller_id=200, state=C.STATE_INSTRUCTION, transferred=True)
        action, _ = C.decide_confirm("TG-TEST01", 999)
        assert action == "not_owner"

    def test_already_final(self):
        seed_offer(seller_id=200, state=C.STATE_CONFIRMED, transferred=True)
        action, _ = C.decide_confirm("TG-TEST01", 200)
        assert action == "already_final"


# ── Инструкция — получатель = buyer, не seller ──────────────────────────
class TestInstructionText:
    def test_instruction_mentions_buyer(self):
        txt = C.build_instruction(100, "STARS", "Foo", "1", "TG-X",
                                  "buyer_name", 999, "http://x")
        assert "@buyer_name" in txt
        assert "@seller" not in txt


# ── Отсутствие ложных «эскроу»/«зачислений» в текстах ────────────────────
class TestNoFraudTexts:
    def test_no_zachisleno_in_instruction(self):
        txt = C.build_instruction(100, "STARS", "Foo", "1", "TG-XX",
                                  "u", 1, "http://x")
        assert "зачислен" not in txt.lower()
        assert "эскроу-систем" not in txt.lower()
        assert "не удерживает" in txt.lower()

    def test_no_zachisleno_in_accepted(self):
        txt = C.build_accepted(100, "STARS", "TG-XX")
        assert "зачислен" not in txt.lower()
        assert "не проводит платёж автоматически" in txt.lower()

    def test_offer_mentions_escrow_only_to_deny_it(self):
        txt = C.build_offer_plain(100, "STARS", "Foo", "1")
        assert "никакой автоматический эскроу" in txt.lower()

    def test_start_disclaims_escrow(self):
        assert "никаких автоматических переводов или эскроу" in C.START_TEXT.lower()


# ── Полный сценарий сделки ────────────────────────────────────────────────
class TestFullScenario:
    def test_offer_accept_confirm_without_auto_detection(self):
        """Полный путь без сработки автодетектора gift_transferred — именно
        этот путь ломался в предыдущей версии."""
        seed_offer(seller_id=200)
        a, _ = C.decide_accept("TG-TEST01", 200)
        assert a == "edit"
        # НЕ вызываем pending_mark_transferred
        a, _ = C.decide_confirm("TG-TEST01", 200)
        assert a == "edit"
        assert C.pending_get("TG-TEST01")["state"] == C.STATE_CONFIRMED

    def test_offer_accept_detected_transfer_confirm(self):
        """Полный путь когда автодетектор сработал."""
        seed_offer(seller_id=200)
        C.decide_accept("TG-TEST01", 200)
        assert C.pending_mark_transferred("TG-TEST01")
        a, _ = C.decide_confirm("TG-TEST01", 200)
        assert a == "edit"

    def test_offer_decline(self):
        seed_offer(seller_id=200)
        a, _ = C.decide_decline("TG-TEST01", 200)
        assert a == "edit"
        a, _ = C.decide_decline("TG-TEST01", 200)
        assert a == "already_final"
