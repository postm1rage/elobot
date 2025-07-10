import discord
import random
from discord.ext import commands
from datetime import datetime
from db_manager import db_manager
from queueing import create_match
from config import MODES


class Tour:
    def __init__(self, bot, tournament_name, participants, slots):
        self.bot = bot
        self.name = tournament_name
        self.participants = participants
        self.slots = slots
        self.current_round = 1
        self.matches = []
        self.winners = []
        self.is_finished = False

    async def start_round(self):
        """Начинает новый раунд турнира"""
        # Если это не первый раунд, участники - победители предыдущего
        if self.current_round > 1:
            self.participants = self.winners
            self.winners = []

        # Заполняем пустые слоты если нужно (только для первого раунда)
        if self.current_round == 1:
            while len(self.participants) < self.slots:
                self.participants.append(
                    {
                        "id": 0,
                        "name": f"emptyslot{len(self.participants)+1}",
                        "mention": "Пустой слот",
                    }
                )

        # Случайное разбиение на пары
        random.shuffle(self.participants)
        pairs = []
        for i in range(0, len(self.participants), 2):
            if i + 1 < len(self.participants):
                pairs.append((self.participants[i], self.participants[i + 1]))

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

        # Отправляем информацию в канал
        await self.send_round_info()

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

        # Если один из игроков - emptyslot, автоматически присуждаем победу
        if player1["id"] == 0 or player2["id"] == 0:
            winner = player2 if player1["id"] == 0 else player1
            match_id = await create_match(
                MODES["station5f"],
                p1_data,
                p2_data,
                matchtype=2,
                tournament_id=self.name,
            )

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

        # Создаем обычный турнирный матч
        cursor = db_manager.get_connection("matches").cursor()
        cursor.execute(
            """INSERT INTO matches 
            (mode, player1, player2, start_time, matchtype, tournament_id) 
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                MODES["station5f"],
                player1["name"],
                player2["name"],
                datetime.now(),
                2,
                self.name,
            ),
        )
        match_id = cursor.lastrowid
        db_manager.get_connection("matches").commit()

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

        matches = db_manager.fetchall(
            "matches",
            """SELECT matchid, player1, player2, isover 
            FROM matches 
            WHERE tournament_id = ? 
            ORDER BY matchid DESC 
            LIMIT ?""",
            (self.name, len(self.matches)),
        )

        embed = discord.Embed(
            title=f"🎮 Турнир {self.name} - Раунд {self.current_round}",
            description="Список матчей текущего раунда:",
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

        embed.set_footer(text=f"Всего матчей: {len(matches)}")
        await channel.send(embed=embed)

    async def check_round_completion(self):
        """Проверяет завершение всех матчей раунда"""
        for match in self.matches:
            if not match["is_finished"]:
                # Проверяем статус матча в БД
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
                    )  # Добавляем победителя в следующий раунд

        # Если все матчи завершены
        if all(m["is_finished"] for m in self.matches):
            if len(self.winners) == 1:
                # Турнир завершен
                await self.finish_tournament()
            else:
                # Начинаем следующий раунд
                self.current_round += 1
                await self.start_round()

        # Если все матчи завершены, переходим к следующему раунду
        if all(m["is_finished"] for m in self.matches):
            if len(self.winners) == 1:
                # Турнир завершен
                await self.finish_tournament()
            else:
                # Начинаем следующий раунд
                self.current_round += 1
                self.participants = self.winners
                self.winners = []
                self.matches = []
                await self.start_round()

    async def finish_tournament(self):
        """Завершает турнир и объявляет победителя"""
        self.is_finished = True
        winner = self.winners[0]

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
            title=f"🎮 Турнирный матч | Раунд {self.current_round}",
            description=f"Турнир: **{self.name}**\nMatch ID: `{match_id}`",
            color=discord.Color.gold(),
        )

        embed.add_field(name="Ваш соперник", value=opponent["name"], inline=False)
        embed.add_field(
            name="Инструкции",
            value="После завершения матча **победитель** должен отправить результат командой:\n"
            f"`.result {match_id} <свой_счет>-<счет_соперника>`\n"
            "Пример: `.result {match_id} 5-3`",
            inline=False,
        )

        await user.send(embed=embed)
