import sqlite3

conn = sqlite3.connect(":memory:")
try:
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    print("FTS5 available")
    print("SQLite version:", sqlite3.sqlite_version)
    # Test external content table
    conn.execute("CREATE TABLE base(id INTEGER PRIMARY KEY, content TEXT)")
    conn.execute(
        "CREATE VIRTUAL TABLE t2 USING fts5(content, content='base', content_rowid='id')"
    )
    print("External content FTS5 available")
    # Test rank
    conn.execute("INSERT INTO base(content) VALUES('hello world')")
    conn.execute("INSERT INTO t2(t2) VALUES('rebuild')")
    rows = conn.execute(
        "SELECT b.id FROM base b JOIN t2 f ON b.id = f.rowid WHERE t2 MATCH 'hello' ORDER BY rank"
    ).fetchall()
    print("MATCH query works, rows:", rows)
except Exception as e:
    print("FTS5 NOT available:", e)
    print("SQLite version:", sqlite3.sqlite_version)
finally:
    conn.close()