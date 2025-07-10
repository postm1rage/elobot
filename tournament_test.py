import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import random
import sqlite3
import os
from config import bot, MODERATOR_ID

# Конфигурация тестовой БД
TEST_DB_FILE = "test_elobot.db"


class TestDBManager:
    def __init__(self):
        self.conn = None
        self.setup_db()

    def setup_db(self):
        """Создает тестовую базу данных"""
        if os.path.exists(TEST_DB_FILE):
            os.remove(TEST_DB_FILE)

        self.conn = sqlite3.connect(TEST_DB_FILE)
        self.create_tables()

    def create_tables(self):
        """Создает таблицы в тестовой БД"""
        cursor = self.conn.cursor()

        # Таблица игроков
        cursor.execute(
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
            in_queue INTEGER DEFAULT 0,
            isbanned BOOLEAN DEFAULT 0,
            isblacklisted BOOLEAN DEFAULT 0
        )
        """
        )

        # Таблица матчей
        cursor.execute(
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
            map TEXT,
            start_time DATETIME,
            matchtype INTEGER DEFAULT 1,
            tournament_id TEXT DEFAULT NULL
        )
        """
        )

        # Таблица турниров
        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            slots INTEGER NOT NULL,
            started BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        # Участники турниров
        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS tournament_participants (
            tournament_id INTEGER,
            user_id TEXT NOT NULL,
            player_name TEXT NOT NULL,
            FOREIGN KEY(tournament_id) REFERENCES tournaments(id),
            PRIMARY KEY(tournament_id, user_id)
        )
        """
        )

        # Баны турниров
        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS tournament_bans (
            tournament_id INTEGER,
            user_id TEXT NOT NULL,
            FOREIGN KEY(tournament_id) REFERENCES tournaments(id),
            PRIMARY KEY(tournament_id, user_id)
        )
        """
        )

        self.conn.commit()

    def execute(self, query, params=()):
        """Выполняет SQL-запрос"""
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        self.conn.commit()
        return cursor

    def fetchall(self, query, params=()):
        """Получает все строки результата"""
        cursor = self.execute(query, params)
        return cursor.fetchall()

    def fetchone(self, query, params=()):
        """Получает одну строку результата"""
        cursor = self.execute(query, params)
        return cursor.fetchone()

    def get_lastrowid(self):
        """Возвращает ID последней вставленной записи"""
        return self.conn.cursor().lastrowid

    def close(self):
        """Закрывает соединение с БД"""
        if self.conn:
            self.conn.close()


# Создаем экземпляр тестовой БД
test_db = TestDBManager()

# Тестовые данные
TEST_TOURNAMENT_NAME = "test-tournament"
TEST_PLAYERS = [
    {"id": "111111", "name": "TestPlayer1"},
    {"id": "222222", "name": "TestPlayer2"},
    {"id": "333333", "name": "TestPlayer3"},
    {"id": "444444", "name": "TestPlayer4"},
    {"id": "555555", "name": "TestPlayer5"},
    {"id": "666666", "name": "TestPlayer6"},
    {"id": "777777", "name": "TestPlayer7"},
    {"id": "888888", "name": "TestPlayer8"},
]


async def setup_test_environment():
    """Создает тестовую среду для турнира"""
    print("Setting up test environment...")

    # Создаем тестовых игроков в базе
    for player in TEST_PLAYERS:
        test_db.execute(
            """INSERT OR IGNORE INTO players 
            (playername, discordid, currentelo) 
            VALUES (?, ?, ?)""",
            (player["name"], player["id"], 1000),
        )

    print("Test players created in database")


async def create_test_tournament():
    """Создает тестовый турнир"""
    print("\nCreating test tournament...")

    # Создаем турнир в БД
    test_db.execute(
        "INSERT INTO tournaments (name, slots) VALUES (?, ?)", (TEST_TOURNAMENT_NAME, 8)
    )

    print(f"Tournament '{TEST_TOURNAMENT_NAME}' created")


async def register_test_players():
    """Регистрирует тестовых игроков на турнир"""
    print("\nRegistering test players...")

    # Получаем ID турнира
    tournament_id = test_db.fetchone(
        "SELECT id FROM tournaments WHERE name = ?", (TEST_TOURNAMENT_NAME,)
    )[0]

    for player in TEST_PLAYERS:
        test_db.execute(
            """INSERT INTO tournament_participants 
            (tournament_id, user_id, player_name) 
            VALUES (?, ?, ?)""",
            (tournament_id, player["id"], player["name"]),
        )

    print(f"Registered {len(TEST_PLAYERS)} players")


async def start_test_tournament():
    """Запускает тестовый турнир"""
    print("\nStarting tournament...")

    # Обновляем статус турнира
    test_db.execute(
        "UPDATE tournaments SET started = 1 WHERE name = ?", (TEST_TOURNAMENT_NAME,)
    )

    print("Tournament started!")


async def simulate_matches():
    """Имитирует завершение матчей турнира"""
    print("\nSimulating matches...")
    
    # Получаем ID турнира
    tournament_id = test_db.fetchone(
        "SELECT id FROM tournaments WHERE name = ?",
        (TEST_TOURNAMENT_NAME,)
    )[0]
    
    # Получаем участников турнира
    participants = test_db.fetchall(
        "SELECT user_id, player_name FROM tournament_participants WHERE tournament_id = ?",
        (tournament_id,)
    )
    
    # Создаем пары для матчей
    for i in range(0, len(participants), 2):
        if i+1 >= len(participants):
            continue
            
        player1_id, player1_name = participants[i]
        player2_id, player2_name = participants[i+1]
        
        # Создаем матч и сразу получаем его ID
        cursor = test_db.conn.cursor()
        cursor.execute(
            """INSERT INTO matches 
            (mode, player1, player2, start_time, matchtype, tournament_id) 
            VALUES (?, ?, ?, ?, ?, ?)""",
            (1, player1_name, player2_name, datetime.now(), 2, TEST_TOURNAMENT_NAME)
        )
        match_id = cursor.lastrowid  # Получаем ID сразу после вставки
        test_db.conn.commit()
        
        # Случайный результат (5-0, 5-1, 5-2, 5-3)
        score1 = 5
        score2 = random.randint(0, 3)
        
        # Определяем победителя
        winner = player1_name if score1 > score2 else player2_name
        
        # Обновляем матч
        test_db.execute(
            """UPDATE matches 
            SET player1score = ?, player2score = ?, isover = 1, isverified = 1
            WHERE matchid = ?""",
            (score1, score2, match_id)
        )
        
        # Обновляем статистику игроков
        test_db.execute(
            "UPDATE players SET wins = wins + 1 WHERE playername = ?",
            (winner,)
        )
        
        loser = player2_name if winner == player1_name else player1_name
        test_db.execute(
            "UPDATE players SET losses = losses + 1 WHERE playername = ?",
            (loser,)
        )
        
        print(f"Match {match_id}: {player1_name} {score1}-{score2} {player2_name} -> Winner: {winner}")


async def check_tournament_progress():
    """Проверяет прогресс турнира"""
    print("\nChecking tournament progress...")

    # Получаем информацию о турнире
    tournament = test_db.fetchone(
        "SELECT id, started FROM tournaments WHERE name = ?", (TEST_TOURNAMENT_NAME,)
    )

    if not tournament:
        print("Tournament not found!")
        return

    t_id, started = tournament

    # Получаем участников
    participants = test_db.fetchall(
        "SELECT player_name FROM tournament_participants WHERE tournament_id = ?",
        (t_id,),
    )

    # Получаем завершенные матчи
    finished_matches = test_db.fetchall(
        """SELECT matchid, player1, player2, player1score, player2score 
        FROM matches 
        WHERE tournament_id = ? AND isover = 1""",
        (TEST_TOURNAMENT_NAME,),
    )

    # Получаем активные матчи
    active_matches = test_db.fetchall(
        """SELECT matchid, player1, player2 
        FROM matches 
        WHERE tournament_id = ? AND isover = 0""",
        (TEST_TOURNAMENT_NAME,),
    )

    print(f"Tournament ID: {t_id}")
    print(f"Started: {'Yes' if started else 'No'}")
    print(f"Participants: {len(participants)}")
    print(f"Finished matches: {len(finished_matches)}")
    print(f"Active matches: {len(active_matches)}")

    if finished_matches:
        print("\nLast 3 finished matches:")
        for match in finished_matches[-3:]:
            print(f"ID: {match[0]} | {match[1]} {match[3]}-{match[4]} {match[2]}")


async def run_tests():
    """Запускает все тесты"""
    try:
        await setup_test_environment()
        await create_test_tournament()
        await register_test_players()
        await start_test_tournament()

        # Имитируем несколько раундов
        for round_num in range(1, 4):
            print(f"\n=== Round {round_num} ===")
            await check_tournament_progress()
            await simulate_matches()
            await asyncio.sleep(1)  # Даем время на обработку

        print("\n=== Final Results ===")
        await check_tournament_progress()
    except Exception as e:
        print(f"Error during tests: {e}")
    finally:
        test_db.close()


@bot.command(name="test_tournament_system")
async def test_tournament_command(ctx):
    """Запускает тестовый сценарий турнира"""
    if ctx.author.id != MODERATOR_ID:
        return await ctx.send("Эта команда только для модераторов")

    await ctx.send("Запускаю тестовый сценарий турнира...")
    await run_tests()
    await ctx.send("Тестирование турнира завершено! Проверьте консоль для деталей.")


def setup(bot):
    # Проверяем, не зарегистрирована ли команда уже
    if not bot.get_command("test_tournament_system"):
        bot.add_command(test_tournament_command)
