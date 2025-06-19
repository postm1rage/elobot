import sqlite3
import discord
from discord.ext import commands

# Карты для черкания
MAPS = ["Бумбокс", "Дуалити", "Зона", "Сандал", "Станция", "Мостик", "Магадан"]

# Конфигурационные константы
VERIFY_CHANNEL_NAME = "elobot-verify"
VERIFICATION_LOGS_CHANNEL_NAME = "elobot-logs"
QUEUE_CHANNEL_NAME = "elobot-queue"
MATCH_RESULTS_CHANNEL_NAME = "elobot-results"
RESULTS_CHANNEL_NAME = "elobot-logs"
MODERATOR_ID = 296821040221388801
VERIFIED_ROLE_NAME = "verified"
DEFAULT_ELO = 1000

# Режимы игры
MODES = {"any": 0, "station5f": 1, "mots": 2, "12min": 3}
MODE_NAMES = {0: "Any", 1: "Station 5 flags", 2: "MotS Solo", 3: "12min"}

LEADERBOARD_MODES = {
    "overall": ("currentelo", "wins", "losses", "ties"),
    "station5flags": (
        "elo_station5f",
        "wins_station5f",
        "losses_station5f",
        "ties_station5f",
    ),
    "mots": ("elo_mots", "wins_mots", "losses_mots", "ties_mots"),
    "12min": ("elo_12min", "wins_12min", "losses_12min", "ties_12min"),
}


# Инициализация базы данных игроков
def init_db():
    db = sqlite3.connect("elobotplayers.db")
    c = db.cursor()

    c.execute(
        """
CREATE TABLE IF NOT EXISTS players (
    playerid INTEGER PRIMARY KEY AUTOINCREMENT,
    playername TEXT NOT NULL UNIQUE,
    discordid TEXT NOT NULL UNIQUE,
    currentelo INTEGER DEFAULT 1000,
    elo_station5f INTEGER DEFAULT 1000,
    elo_mots INTEGER DEFAULT 1000,
    elo_12min INTEGER DEFAULT 1000,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    ties INTEGER DEFAULT 0,
    wins_station5f INTEGER DEFAULT 0,
    losses_station5f INTEGER DEFAULT 0,
    ties_station5f INTEGER DEFAULT 0,
    wins_mots INTEGER DEFAULT 0,
    losses_mots INTEGER DEFAULT 0,
    ties_mots INTEGER DEFAULT 0,
    wins_12min INTEGER DEFAULT 0,
    losses_12min INTEGER DEFAULT 0,
    ties_12min INTEGER DEFAULT 0,
    currentmatches INTEGER DEFAULT 0,
    in_queue INTEGER DEFAULT 0
)
"""
    )
    db.commit()
    return db


# Инициализация базы данных матчей
def init_matches_db():
    matches_db = sqlite3.connect("elobotmatches.db")
    c = matches_db.cursor()

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS matches (
        matchid INTEGER PRIMARY KEY AUTOINCREMENT,
        mode INTEGER NOT NULL,
        player1 TEXT NOT NULL,
        player2 TEXT NOT NULL,
        isover INTEGER DEFAULT 0,
        player1score INTEGER,
        player2score INTEGER,
        isverified INTEGER DEFAULT 0,
        map TEXT
    )
    """
    )
    # Проверяем существование колонки map
    c.execute("PRAGMA table_info(matches)")
    columns = [info[1] for info in c.fetchall()]

    if "map" not in columns:
        # Добавляем колонку map
        c.execute("ALTER TABLE matches ADD COLUMN map TEXT")
        print("Добавлена колонка 'map' в таблицу 'matches'")

    matches_db.commit()
    return matches_db


# Инициализация бота
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    intents=intents,
    command_prefix="!",
)

# Инициализация баз данных
db = init_db()
matches_db = init_matches_db()
