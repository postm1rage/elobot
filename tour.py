import discord
import json
import random
from discord.ext import commands
from datetime import datetime
from db_manager import db_manager
from queueing import create_match
from config import MODES
import asyncio


class Tour:
    def __init__(self, bot, tournament_name, participants, slots, cog):
        self.bot = bot
        self.name = tournament_name
        self.participants = participants
        self.slots = slots
        self.current_round = 1
        self.matches = []
        self.winners = []
        self.is_finished = False
        self.cog = cog
        self.tournament_id = None  # Добавляем ID турнира

    async def save_state(self):
        """Сохраняет текущее состояние турнира в БД"""
        tour_data = {
            "current_round": self.current_round,
            "participants": json.dumps(self.participants),
            "winners": json.dumps(self.winners),
            "matches": json.dumps(
                [
                    {
                        "id": m["id"],
                        "player1": m["player1"],
                        "player2": m["player2"],
                        "winner": m["winner"],
                        "is_finished": m["is_finished"],
                    }
                    for m in self.matches
                ]
            ),
        }

        # Получаем ID турнира если еще не знаем
        if not self.tournament_id:
            tour = db_manager.fetchone(
                "tournaments", "SELECT id FROM tournaments WHERE name = ?", (self.name,)
            )
            if tour:
                self.tournament_id = tour[0]

        if self.tournament_id:
            # Удаляем старую запись если есть
            db_manager.execute(
                "tournaments",
                "DELETE FROM active_tours WHERE tournament_id = ?",
                (self.tournament_id,),
            )

            # Вставляем новую
            db_manager.execute(
                "tournaments",
                """INSERT INTO active_tours 
                (tournament_id, current_round, participants, winners, matches)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    self.tournament_id,
                    tour_data["current_round"],
                    tour_data["participants"],
                    tour_data["winners"],
                    tour_data["matches"],
                ),
            )

            async def load_state(self):
                """Загружает состояние турнира из БД"""
                import json

                tour = db_manager.fetchone(
                    "tournaments",
                    """SELECT id, current_round, participants, winners, matches 
                    FROM active_tours 
                    WHERE tournament_id = (
                        SELECT id FROM tournaments WHERE name = ?
                    )""",
                    (self.name,),
                )

                if tour:
                    self.tournament_id = tour[0]
                    self.current_round = tour[1]
                    self.participants = json.loads(tour[2])
                    self.winners = json.loads(tour[3])

                    # Восстанавливаем матчи
                    self.matches = []
                    for m in json.loads(tour[4]):
                        self.matches.append(
                            {
                                "id": m["id"],
                                "player1": m["player1"],
                                "player2": m["player2"],
                                "winner": m["winner"],
                                "is_finished": m["is_finished"],
                            }
                        )

                    return True
                return False

    async def start_round(self):
        """Начинает новый тур турнира"""
        # Если это не первый тур, участники - победители предыдущего
        if self.current_round > 1:
            self.participants = self.winners
            self.winners = []

        # Удаляем дубликаты участников
        unique_participants = []
        seen_ids = set()
        for p in self.participants:
            if p["id"] not in seen_ids:
                seen_ids.add(p["id"])
                unique_participants.append(p)
        self.participants = unique_participants

        # Заполняем пустые слоты если нужно (только для первого тура)
        if self.current_round == 1:
            while len(self.participants) < self.slots:
                self.participants.append(
                    {
                        "id": 0,
                        "name": f"emptyslot{len(self.participants)+1}",
                        "mention": "Пустой слот",
                    }
                )

        # Случайное разбиение на пары, исключая дубликаты
        random.shuffle(self.participants)
        pairs = []
        used_ids = set()

        for i in range(len(self.participants)):
            if self.participants[i]["id"] in used_ids:
                continue

            # Ищем следующего доступного участника
            for j in range(i + 1, len(self.participants)):
                if (
                    self.participants[j]["id"] not in used_ids
                    and self.participants[j]["id"] != self.participants[i]["id"]
                ):

                    pairs.append((self.participants[i], self.participants[j]))
                    used_ids.add(self.participants[i]["id"])
                    used_ids.add(self.participants[j]["id"])
                    break

        # Создаем матчи
        self.matches = []
        for player1, player2 in pairs:
            # Пропускаем пары с двумя emptyslot
            if player1["id"] == 0 and player2["id"] == 0:
                continue

            match_id = await self.create_tournament_match(player1, player2)
            self.matches.append(
                {
                    "id": match_id,
                    "player1": player1,
                    "player2": player2,
                    "winner": None,
                    "is_finished": False,
                }
            )

        # Если остался нечетный участник - автоматически проходит дальше
        if len(used_ids) < len(self.participants):
            remaining = [p for p in self.participants if p["id"] not in used_ids]
            if remaining:
                lucky_player = remaining[0]
                self.winners.append(lucky_player)

                # Уведомление о автоматическом прохождении
                if lucky_player["id"] != 0:  # Если это не пустой слот
                    try:
                        user = await self.bot.fetch_user(lucky_player["id"])
                        await user.send(
                            f"🎉 В турнире {self.name} (тур {self.current_round}) "
                            f"у вас не оказалось соперника, поэтому вы автоматически проходите в следующий тур!"
                        )
                    except Exception as e:
                        print(f"Не удалось уведомить игрока: {e}")

        # Отправляем информацию в канал
        await self.send_round_info()
        await self.save.state()

    async def create_tournament_match(self, player1, player2):
        """Создает турнирный матч и уведомляет реальных игроков"""
        # Для реальных игроков получаем данные из базы
        p1_data = {
            "discord_id": player1["id"],
            "nickname": player1["name"],
            "rating": (
                db_manager.fetchone(
                    "players",
                    "SELECT currentelo FROM players WHERE discordid = ?",
                    (str(player1["id"]),),
                )[0]
                if player1["id"] != 0
                else 0
            ),
            "channel_id": None,
            "join_time": datetime.now(),
        }

        p2_data = {
            "discord_id": player2["id"],
            "nickname": player2["name"],
            "rating": (
                db_manager.fetchone(
                    "players",
                    "SELECT currentelo FROM players WHERE discordid = ?",
                    (str(player2["id"]),),
                )[0]
                if player2["id"] != 0
                else 0
            ),
            "channel_id": None,
            "join_time": datetime.now(),
        }

        # Создаем обычный турнирный матч
        cursor = db_manager.execute(
            "matches",
            """INSERT INTO matches 
            (mode, player1, player2, start_time, matchtype, tournament_id) 
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                MODES["station5f"],
                player1["name"],
                player2["name"],
                datetime.now(),
                2,  # matchtype = 2 для турнирных матчей
                self.name,  # Используем название турнира как ID
            ),
        )
        match_id = cursor.lastrowid
        db_manager.get_connection("matches").commit()

        # Если один из игроков - emptyslot, автоматически присуждаем победу
        if player1["id"] == 0 or player2["id"] == 0:
            winner = player2 if player1["id"] == 0 else player1
            # Помечаем матч как завершенный
            winner_score = 1 if winner["id"] == player1["id"] else 0
            loser_score = 1 - winner_score

            db_manager.execute(
                "matches",
                """
                UPDATE matches 
                SET player1score = ?, player2score = ?, isover = 1, isverified = 1
                WHERE matchid = ?
                """,
                (winner_score, loser_score, match_id),
            )

            # Добавляем победителя
            self.winners.append(winner)
            return match_id

        # Отправляем уведомления только реальным игрокам
        if player1["id"] != 0:
            try:
                user = await self.bot.fetch_user(player1["id"])
                await self.send_match_notification(match_id, player1, player2, user)
            except Exception as e:
                print(f"Не удалось отправить уведомление игроку {player1['name']}: {e}")

        if player2["id"] != 0:
            try:
                user = await self.bot.fetch_user(player2["id"])
                await self.send_match_notification(match_id, player2, player1, user)
            except Exception as e:
                print(f"Не удалось отправить уведомление игроку {player2['name']}: {e}")

        return match_id

    async def send_round_info(self):
        channel = discord.utils.get(
            self.bot.get_all_channels(), name=f"{self.name}-matches"
        )
        if not channel:
            print(f"⚠ Канал {self.name}-matches не найден")
            return

        # Получаем только актуальные матчи текущего тура
        matches = db_manager.fetchall(
            "matches",
            """SELECT matchid, player1, player2, isover 
            FROM matches 
            WHERE tournament_id = ? 
            AND matchid IN ({})
            ORDER BY matchid""".format(
                ",".join("?" for _ in self.matches)
            ),
            (self.name, *(m["id"] for m in self.matches)),
        )

        embed = discord.Embed(
            title=f"🎮 Турнир {self.name} - Тур {self.current_round}",
            description="Список матчей текущего тура:",
            color=discord.Color.gold(),
        )

        for match in matches:
            match_id, player1, player2, isover = match
            status = "Завершен" if isover else "В процессе"
            embed.add_field(
                name=f"Матч #{match_id} ({status})",
                value=f"{player1} vs {player2}",
                inline=False,
            )

        # Добавляем информацию об автоматически прошедших
        if len(self.winners) > len(self.matches):
            auto_qualified = [
                w
                for w in self.winners
                if w
                not in [m["player1"] for m in self.matches]
                + [m["player2"] for m in self.matches]
            ]
            if auto_qualified:
                names = ", ".join(p["name"] for p in auto_qualified)
                embed.add_field(
                    name="Автоматически проходят",
                    value=f"Следующие участники получают автоматический проход: {names}",
                    inline=False,
                )

        embed.set_footer(text=f"Всего матчей: {len(matches)}")
        await channel.send(embed=embed)

    async def check_round_completion(self):
        """Проверяет завершение всех матчей тура"""
        # Сначала обновляем информацию о завершенных матчах
        for match in self.matches:
            if not match["is_finished"]:
                match_data = db_manager.fetchone(
                    "matches",
                    "SELECT isover, player1score, player2score FROM matches WHERE matchid = ?",
                    (match["id"],),
                )

                if match_data and match_data[0] == 1:  # Если матч завершен
                    match["is_finished"] = True
                    isover, p1_score, p2_score = match_data

                    # Определяем победителя
                    if p1_score > p2_score:
                        winner = match["player1"]
                    else:
                        winner = match["player2"]

                    match["winner"] = winner
                    self.winners.append(
                        winner
                    )  # Добавляем победителя в следующий тур

        # Если все матчи завершены или их нет (принудительный переход)
        if all(m["is_finished"] for m in self.matches) or not self.matches:
            if len(self.winners) == 1:
                # Турнир завершен
                await self.finish_tournament()
            else:
                # Начинаем следующий тур
                self.current_round += 1
                self.matches = []  # Очищаем текущие матчи
                await self.start_round()

        await self.save_state()

    async def finish_tournament(self):
        """Завершает турнир и объявляет победителя"""
        self.is_finished = True
        winner = self.winners[0]

        # Удаляем запись об активном турнире
        if self.tournament_id:
            db_manager.execute(
                "tournaments",
                "DELETE FROM active_tours WHERE tournament_id = ?",
                (self.tournament_id,)
            )

        # Создаем embed для объявления победителя
        embed = discord.Embed(
            title=f"🏆 Турнир {self.name} завершен!",
            description=f"Поздравляем победителя:\n**{winner['name']}**",
            color=discord.Color.gold(),
        )

        # Отправляем в канал результатов
        results_channel = discord.utils.get(
            self.bot.get_all_channels(), name=f"{self.name}-results"
        )

        if results_channel:
            await results_channel.send(embed=embed)

        # Отправляем личное сообщение победителю
        if winner["id"] != 0:  # Если это не пустой слот
            try:
                user = await self.bot.fetch_user(winner["id"])
                winner_embed = discord.Embed(
                    title=f"🏆 Победа в турнире {self.name}!",
                    description="Поздравляем с победой!",
                    color=discord.Color.gold(),
                )
                await user.send(embed=winner_embed)
            except Exception as e:
                print(f"Не удалось отправить уведомление победителю: {e}")

        if self.cog and self.name in self.cog.active_tours:
            del self.cog.active_tours[self.name]

    async def set_winner(self, match_id, winner_name):
        
        """Вручную устанавливает победителя матча"""
        match = next((m for m in self.matches if m["id"] == match_id), None)
        if not match:
            return False

        winner = next(
            (
                p
                for p in [match["player1"], match["player2"]]
                if p["name"] == winner_name
            ),
            None,
        )

        if not winner:
            return False

        # Обновляем матч в БД
        winner_score = 1 if winner["id"] == match["player1"]["id"] else 0
        loser_score = 1 - winner_score

        db_manager.execute(
            "matches",
            """
            UPDATE matches 
            SET player1score = ?, player2score = ?, isover = 1, isverified = 1
            WHERE matchid = ?
            """,
            (winner_score, loser_score, match_id),
        )

        # Обновляем информацию о матче
        match["winner"] = winner
        match["is_finished"] = True
        self.winners.append(winner)

        return True

    async def send_match_notification(self, match_id, player, opponent, user):
        """Отправляет уведомление о матче конкретному игроку"""
        embed = discord.Embed(
            title=f"🎮 Турнирный матч | Тур {self.current_round}",
            description=f"Турнир: **{self.name}**\nMatch ID: `{match_id}`",
            color=discord.Color.gold(),
        )

        embed.add_field(name="Ваш соперник", value=opponent["name"], inline=False)
        embed.add_field(
            name="Инструкции",
            value="После завершения матча **победитель** должен отправить результат в канал для результатов турнира",
            inline=False,
        )

        await user.send(embed=embed)
