from inbox.config import Firm
from inbox.helpdesk import import_helpdesk
from inbox.store import connect


def test_reimporting_history_never_touches_live_mail(tmp_path):
    corpus = tmp_path / "pairs.yaml"
    corpus.write_text(
        "pairs:\n- {thread_id: t1, customer_id: c1, asked_at: '2026-01-01T10:00:00+00:00', "
        "question: 'where are my keys', answer: 'Hi Ann, at the office. Kind regards'}\n",
        encoding="utf-8",
    )
    firm = Firm("f", "F", "UTC", "helpdesk", corpus, "brand", tmp_path, [], {})
    import_helpdesk(firm, log=lambda *a: None)
    conn = connect(firm.db_path)
    with conn:
        conn.execute("INSERT INTO messages VALUES ('18c2f9a0b1d2e3f4','g1',NULL,'lina@x.nl',1,'2026-09-27T10:00:00+00:00','live mail')")
        conn.execute("INSERT INTO messages VALUES ('demo-abc-0-1','d1',NULL,'demo-abc',1,'2026-09-27T10:00:00+00:00','visitor mail')")
    conn.close()
    import_helpdesk(firm, log=lambda *a: None)
    conn = connect(firm.db_path)
    texts = {r[0] for r in conn.execute("SELECT text FROM messages")}
    assert {"live mail", "visitor mail", "where are my keys"} <= texts
    assert conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0] == 1
