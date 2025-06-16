import sqlite3
import discord
from discord.ext import commands

# Инициализация базы данных
def init_db():
    db = sqlite3.connect("elobotplayers.db")
    c = db.cursor()

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

    db.commit()
    return db

# Конфигурационные константы
VERIFY_CHANNEL_NAME = "elobot-verify"
RESULTS_CHANNEL_NAME = "elobot-logs"
MODERATOR_ID = 296821040221388801
VERIFIED_ROLE_NAME = "verified"
DEFAULT_ELO = 1000

# Инициализация бота
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    intents=intents,
    command_prefix="!",
)

# Инициализация базы данных
db = init_db()