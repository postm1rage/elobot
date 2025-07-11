import discord
from discord.ext import commands
from db_manager import db_manager  # Импорт менеджера БД

# Карты для черкания
MAPS = ["Бумбокс", "Дуалити", "Зона", "Сандал", "Станция", "Мостик", "Магадан"]

# Конфигурационные константы
VERIFY_CHANNEL_NAME = "elobot-verify"
VERIFICATION_LOGS_CHANNEL_NAME = "elobot-logs"
QUEUE_CHANNEL_NAME = "elobot-queue"
MATCH_RESULTS_CHANNEL_NAME = "elobot-results"
RESULTS_CHANNEL_NAME = "elobot-logs"
MODERATOR_ID = 710147702490660914  # illumi: 710147702490660914 мой: 296821040221388801
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

# Инициализация бота
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True

bot = commands.Bot(
    intents=intents,
    command_prefix=".",
)

# Удалил старую инициализацию бд, добавил её в db_manager
# init_db() и init_matches_db() больше не нужны


# Добавляем вспомогательные функции для удобства
def get_player_db():
    """Возвращает соединение с базой игроков"""
    return db_manager.get_connection("players")


def get_matches_db():
    """Возвращает соединение с базой матчей"""
    return db_manager.get_connection("matches")


def execute_players(query, params=()):
    """Выполняет запрос к базе игроков"""
    return db_manager.execute("players", query, params)


def execute_matches(query, params=()):
    """Выполняет запрос к базе матчей"""
    return db_manager.execute("matches", query, params)


def fetch_player(query, params=()):
    """Получает одну запись из базы игроков"""
    return db_manager.fetchone("players", query, params)


def fetch_match(query, params=()):
    """Получает одну запись из базы матчей"""
    return db_manager.fetchone("matches", query, params)
