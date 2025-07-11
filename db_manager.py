import sqlite3
import threading
import logging
from functools import wraps

# Настройка логгера
logger = logging.getLogger("db_manager")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logger.addHandler(handler)


class DBManager:
    def __init__(self):
        self._connections = {}
        self._lock = threading.Lock()
        self._db_files = {
            "players": "elobotplayers.db",
            "matches": "elobotmatches.db",
            "tournaments": "elotournaments.db",
        }

        # Инициализация таблиц при первом запуске
        self._initialize_databases()

    def _initialize_databases(self):
        """Создает таблицы если они не существуют и добавляет недостающие колонки"""
        with self._lock:
            # Инициализация базы игроков
            conn = sqlite3.connect(self._db_files["players"])
            try:
                conn.execute("""
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
                """)
                conn.commit()
                logger.info("Players database initialized")
            except Exception as e:
                logger.error(f"Error initializing players database: {e}")
                raise
            finally:
                conn.close()

            # Инициализация базы матчей
            conn = sqlite3.connect(self._db_files["matches"])
            try:
                conn.execute("""
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
                    tournament_id TEXT DEFAULT 0
                )
                """)
                conn.commit()
                logger.info("Matches database initialized")
            except Exception as e:
                logger.error(f"Error initializing matches database: {e}")
                raise
            finally:
                conn.close()

            # Инициализация базы турниров
            conn = sqlite3.connect(self._db_files["tournaments"])
            try:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS tournaments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    slots INTEGER NOT NULL,
                    started BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """)

                conn.execute("""
                CREATE TABLE IF NOT EXISTS tournament_bans (
                    tournament_id INTEGER,
                    user_id TEXT NOT NULL,
                    FOREIGN KEY(tournament_id) REFERENCES tournaments(id),
                    PRIMARY KEY(tournament_id, user_id)
                )
                """)
                conn.commit()
                logger.info("Tournaments database initialized")
            except Exception as e:
                logger.error(f"Error initializing tournaments database: {e}")
                raise
            finally:
                conn.close()

    def get_connection(self, db_type):
        """Получает соединение с базой (создает при необходимости)"""
        with self._lock:
            if db_type not in self._db_files:
                raise ValueError(f"Unknown database type: {db_type}")

            db_file = self._db_files[db_type]

            if db_type not in self._connections or self._connections[db_type] is None:
                self._connections[db_type] = sqlite3.connect(db_file)
                # Оптимизации для SQLite
                self._connections[db_type].execute("PRAGMA journal_mode=WAL")
                self._connections[db_type].execute("PRAGMA synchronous=NORMAL")
                self._connections[db_type].execute("PRAGMA foreign_keys=ON")
                logger.info(f"Created new connection for {db_type}")

            return self._connections[db_type]

    def reconnect(self, db_type):
        """Переподключается к базе"""
        with self._lock:
            if db_type in self._connections:
                try:
                    self._connections[db_type].close()
                except Exception as e:
                    logger.error(f"Error closing connection: {e}")

            self._connections[db_type] = None
            return self.get_connection(db_type)

    def execute(self, db_type, query, params=(), retry=True):
        """Выполняет SQL-запрос с обработкой ошибок соединения"""
        try:
            conn = self.get_connection(db_type)
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor
        except (sqlite3.ProgrammingError, sqlite3.OperationalError) as e:
            if "closed" in str(e) and retry:
                logger.warning(f"Connection closed, reconnecting... (Error: {e})")
                conn = self.reconnect(db_type)
                return self.execute(db_type, query, params, retry=False)
            raise
        except Exception as e:
            logger.error(f"Database error: {e}")
            raise

    def fetchall(self, db_type, query, params=()):
        """Выполняет запрос и возвращает все строки"""
        cursor = self.execute(db_type, query, params)
        return cursor.fetchall()

    def fetchone(self, db_type, query, params=()):
        """Выполняет запрос и возвращает одну строку"""
        cursor = self.execute(db_type, query, params)
        return cursor.fetchone()

    def close_all(self):
        """Закрывает все соединения"""
        with self._lock:
            for db_type, conn in list(self._connections.items()):
                try:
                    if conn:
                        conn.close()
                        logger.info(f"Closed connection for {db_type}")
                except Exception as e:
                    logger.error(f"Error closing connection for {db_type}: {e}")
                finally:
                    self._connections[db_type] = None

    def get_lastrowid(self, db_type):
        """Возвращает ID последней вставленной записи"""
        conn = self.get_connection(db_type)
        return conn.cursor().lastrowid

    def check_column_exists(self, db_type, table, column):
        """Проверяет существование колонки в таблице"""
        try:
            conn = self.get_connection(db_type)
            cursor = conn.execute(f"PRAGMA table_info({table})")
            columns = [col[1] for col in cursor.fetchall()]
            return column in columns
        except Exception as e:
            logger.error(f"Error checking column existence: {e}")
            return False


# Глобальный экземпляр менеджера БД
db_manager = DBManager()


# Декоратор для автоматического управления соединениями
def with_db(db_type):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                conn = db_manager.get_connection(db_type)
                return func(conn, *args, **kwargs)
            except (sqlite3.ProgrammingError, sqlite3.OperationalError) as e:
                if "closed" in str(e):
                    conn = db_manager.reconnect(db_type)
                    return func(conn, *args, **kwargs)
                raise
            except Exception as e:
                logger.error(f"Database operation failed: {e}")
                raise

        return wrapper

    return decorator