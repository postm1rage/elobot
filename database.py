import sqlite3

class Database:
    def __init__(self):
        self.conn = sqlite3.connect("elobotplayers.db")
        self._init_db()

    def _init_db(self):
        c = self.conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                playerid INTEGER PRIMARY KEY AUTOINCREMENT,
                playername TEXT NOT NULL UNIQUE,
                discordid TEXT NOT NULL UNIQUE,
                leaderboardplace INTEGER DEFAULT 0,
                currentelo INTEGER DEFAULT 1000,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                ties INTEGER DEFAULT 0,
                currentmatches INTEGER DEFAULT 0
            )
            """
        )
        self.conn.commit()

    def close(self):
        self.conn.close()

db = Database()