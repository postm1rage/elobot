import discord
from discord.ui import View, Button, Select
from config import (
    MODES,
    MODE_NAMES,
    MAPS,
    MODERATOR_ID,
)
from db_manager import db_manager
import asyncio
import sqlite3
from datetime import datetime, timedelta

import random
import re

RESULT_REMINDER = (
    "ℹ️ После завершения матча **победитель** должен отправить результат командой `.result <ID_матча> <свой_счет>-<счет_соперника>` "
    "в личные сообщения боту, приложив скриншот. Пример: `.result 123 5-3`\n"
    "❗ Учтите: в счете первым числом указывается счет победителя (большее число), вторым - проигравшего (меньшее число)."
)

# Глобальный словарь для хранения репортов
pending_reports = {}
# Глобальные словари для новой системы подтверждения
pending_player_confirmations = {}  # {match_id: {data}}
player_confirmation_views = {}  # {message_id: view}


class ModeratorResolutionView(View):
    def __init__(self, match_id):
        super().__init__(timeout=None)
        self.match_id = match_id

    @discord.ui.button(label="Подтвердить", style=discord.ButtonStyle.green)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Получаем данные о результате напрямую из БД
        match_data = db_manager.execute(
            "matches",
            "SELECT player1, player2, mode FROM matches WHERE matchid = ?",
            (self.match_id,),
        ).fetchone()

        if not match_data:
            await interaction.response.send_message("❌ Матч не найден", ephemeral=True)
            return

        player1, player2, mode = match_data

        # Получаем результат из pending_player_confirmations
        result_data = pending_player_confirmations.get(self.match_id)
        if not result_data:
            await interaction.response.send_message(
                "❌ Данные о результате не найдены", ephemeral=True
            )
            return

        scores = result_data["scores"]
        if not re.match(r"^\d+-\d+$", scores):
            await interaction.response.send_message(
                "❌ Неверный формат счета", ephemeral=True
            )
            return

        score1, score2 = map(int, scores.split("-"))

        # Обрабатываем результат
        await self.process_match_result(player1, player2, mode, score1, score2)

        # Удаляем из ожидания
        if self.match_id in pending_player_confirmations:
            del pending_player_confirmations[self.match_id]

        await interaction.response.send_message(
            "✅ Результат подтвержден модератором!", ephemeral=True
        )

        # Уведомляем игроков
        await self.notify_players("подтвержден модератором")
        await interaction.message.delete()

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Возвращаем матч в активное состояние
        db_manager.execute(
            "matches",
            "UPDATE matches SET isover = 0 WHERE matchid = ?",
            (self.match_id,),
        )
        # Удаляем из ожидания
        if self.match_id in pending_player_confirmations:
            # Получаем данные для уведомления отправителя
            result_data = pending_player_confirmations[self.match_id]
            submitter_id = result_data["submitter_id"]
            del pending_player_confirmations[self.match_id]

            # Уведомляем отправителя
            try:
                submitter_user = await global_bot.fetch_user(submitter_id)
                await submitter_user.send(
                    f"❌ Результат матча #{self.match_id} отклонен модератором."
                )
            except:
                pass

        await interaction.response.send_message(
            "✅ Результат отклонен!", ephemeral=True
        )

        # Уведомляем игроков
        await self.notify_players("отклонен модератором")
        await interaction.message.delete()

    @discord.ui.button(label="Тех. поражение", style=discord.ButtonStyle.gray)
    async def tech_loss(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Получаем данные о матче
        match_data = db_manager.execute(
            "matches",
            "SELECT mode, player1, player2 FROM matches WHERE matchid = ?",
            (self.match_id,),
        ).fetchone()

        if not match_data:
            await interaction.response.send_message("❌ Матч не найден", ephemeral=True)
            return

        mode, player1, player2 = match_data

        # Получаем информацию о том, кто отправил результат
        result_data = pending_player_confirmations.get(self.match_id)
        if not result_data:
            await interaction.response.send_message(
                "❌ Данные о результате не найдены", ephemeral=True
            )
            return

        winner = result_data["submitter_name"]
        loser = player2 if winner == player1 else player1

        # Применяем техническое поражение
        await self.apply_tech_loss(mode, player1, player2, winner, loser)

        # Удаляем из ожидания
        if self.match_id in pending_player_confirmations:
            del pending_player_confirmations[self.match_id]

        await interaction.response.send_message(
            "✅ Техническое поражение применено!", ephemeral=True
        )

        # Уведомляем игроков
        await self.notify_players(f"завершен техническим поражением в пользу {winner}")
        await interaction.message.delete()

    async def process_match_result(self, player1, player2, mode, score1, score2):
        """Обработка подтвержденного результата"""
        try:
            # Определяем победителя
            if score1 > score2:
                winner = player1
                loser = player2
            else:
                winner = player2
                loser = player1

            # Получаем текущие рейтинги
            rating_winner = get_player_rating(winner, mode)
            rating_loser = get_player_rating(loser, mode)

            # Рассчитываем новые рейтинги
            new_rating_winner, new_rating_loser = calculate_elo(
                rating_winner, rating_loser, 1
            )

            # Обновляем статистику
            db_manager.execute(
                "players",
                "UPDATE players SET wins = wins + 1 WHERE playername = ?",
                (winner,),
            )
            db_manager.execute(
                "players",
                "UPDATE players SET losses = losses + 1 WHERE playername = ?",
                (loser,),
            )

            # Обновляем ELO
            update_player_rating(winner, new_rating_winner, mode)
            update_player_rating(loser, new_rating_loser, mode)

            # Обновляем запись матча
            db_manager.execute(
                "matches",
                "UPDATE matches SET player1score = ?, player2score = ?, isover = 1, isverified = 1 WHERE matchid = ?",
                (score1, score2, self.match_id),
            )
            # Отправляем результат в канал
            mode_name = MODE_NAMES.get(mode, "Unknown")
            embed = discord.Embed(
                title=f"🏁 Матч завершен | ID: {self.match_id}",
                description=(
                    f"**Режим:** {mode_name}\n"
                    f"**Игроки:** {player1} vs {player2}\n"
                    f"**Счет:** {score1} - {score2}\n"
                    f"**Победитель:** {winner}\n\n"
                    f"**Изменения ELO:**\n"
                    f"{winner}: {rating_winner} → **{new_rating_winner}** (+{new_rating_winner - rating_winner})\n"
                    f"{loser}: {rating_loser} → **{new_rating_loser}** ({new_rating_loser - rating_loser})"
                ),
                color=discord.Color.green(),
            )

            # Ищем канал результатов
            results_channel = None
            for guild in global_bot.guilds:
                for channel in guild.text_channels:
                    if channel.name == "elobot-results":
                        results_channel = channel
                        break
                if results_channel:
                    break

            if results_channel:
                await results_channel.send(embed=embed)
            else:
                print("⚠ Канал elobot-results не найден")

            # Отправляем результат игрокам в ЛС
            try:
                # Получаем discord_id игроков
                player1_data = db_manager.execute(
                    "players",
                    "SELECT discordid FROM players WHERE playername = ?",
                    (player1,),
                ).fetchone()
                player2_data = db_manager.execute(
                    "players",
                    "SELECT discordid FROM players WHERE playername = ?",
                    (player2,),
                ).fetchone()
            except Exception as e:
                print(f"Ошибка отправки результата игрокам: {e}")

        except Exception as e:
            print(f"Ошибка обработки результата: {e}")
            # Уведомляем модератора об ошибке
            try:
                moderator = await global_bot.fetch_user(MODERATOR_ID)
                await moderator.send(
                    f"❌ Ошибка обработки результата матча #{self.match_id}: {str(e)}"
                )
            except:
                pass

    async def apply_tech_loss(self, mode, player1, player2, winner, loser):
        """Применение технического поражения"""
        try:
            # Получаем текущие рейтинги
            rating_winner = get_player_rating(winner, mode)
            rating_loser = get_player_rating(loser, mode)

            # Рассчитываем новые рейтинги (техническая победа)
            new_rating_winner, new_rating_loser = calculate_elo(
                rating_winner, rating_loser, 1
            )

            # Обновляем статистику
            db_manager.execute(
                "players",
                "UPDATE players SET wins = wins + 1 WHERE playername = ?",
                (winner,),
            )
            db_manager.execute(
                "players",
                "UPDATE players SET losses = losses + 1 WHERE playername = ?",
                (loser,),
            )

            # Обновляем ELO
            update_player_rating(winner, new_rating_winner, mode)
            update_player_rating(loser, new_rating_loser, mode)

            # Обновляем запись матча
            if winner == player1:
                db_manager.execute(
                    "matches",
                    "UPDATE matches SET player1score = 1, player2score = 0, isover = 1, isverified = 1 WHERE matchid = ?",
                    (self.match_id,),
                )
            else:
                db_manager.execute(
                    "matches",
                    "UPDATE matches SET player1score = 0, player2score = 1, isover = 1, isverified = 1 WHERE matchid = ?",
                    (self.match_id,),
                )

            # Отправляем результат в канал
            mode_name = MODE_NAMES.get(mode, "Unknown")
            embed = discord.Embed(
                title=f"⚠️ Матч завершен (тех. поражение) | ID: {self.match_id}",
                description=(
                    f"**Режим:** {mode_name}\n"
                    f"**Игроки:** {player1} vs {player2}\n"
                    f"**Победитель:** {winner} (техническое поражение)\n\n"
                    f"**Изменения ELO:**\n"
                    f"{winner}: {rating_winner} → **{new_rating_winner}** (+{new_rating_winner - rating_winner})\n"
                    f"{loser}: {rating_loser} → **{new_rating_loser}** ({new_rating_loser - rating_loser})"
                ),
                color=discord.Color.red(),
            )

            # Ищем канал результатов
            results_channel = None
            for guild in global_bot.guilds:
                for channel in guild.text_channels:
                    if channel.name == "elobot-results":
                        results_channel = channel
                        break
                if results_channel:
                    break

            if results_channel:
                await results_channel.send(embed=embed)
            else:
                print("⚠ Канал elobot-results не найден")

            # Отправляем результат игрокам в ЛС
            try:
                # Получаем discord_id игроков
                player1_data = db_manager.execute(
                    "players",
                    "SELECT discordid FROM players WHERE playername = ?",
                    (player1,),
                ).fetchone()
                player2_data = db_manager.execute(
                    "players",
                    "SELECT discordid FROM players WHERE playername = ?",
                    (player2,),
                ).fetchone()

                if player1_data:
                    user1 = await global_bot.fetch_user(int(player1_data[0]))
                    await user1.send(
                        f"ℹ️ Ваш матч #{self.match_id} завершен техническим поражением"
                    )
                    await user1.send(embed=embed)

                if player2_data:
                    user2 = await global_bot.fetch_user(int(player2_data[0]))
                    await user2.send(
                        f"ℹ️ Ваш матч #{self.match_id} завершен техническим поражением"
                    )
                    await user2.send(embed=embed)
            except Exception as e:
                print(f"Ошибка отправки результата игрокам: {e}")

        except Exception as e:
            print(f"Ошибка применения тех. поражения: {e}")
            # Уведомляем модератора об ошибке
            try:
                moderator = await global_bot.fetch_user(MODERATOR_ID)
                await moderator.send(
                    f"❌ Ошибка применения тех. поражения для матча #{self.match_id}: {str(e)}"
                )
            except:
                pass

    async def notify_players(self, action):
        """Уведомление игроков о действии модератора"""
        # Получаем данные о матче
        match_data = db_manager.execute(
            "matches",
            "SELECT player1, player2 FROM matches WHERE matchid = ?",
            (self.match_id,),
        ).fetchone()

        if not match_data:
            return

        player1, player2 = match_data

        # Получаем discord_id игроков
        player1_data = db_manager.execute(
            "players", "SELECT discordid FROM players WHERE playername = ?", (player1,)
        ).fetchone()
        player2_data = db_manager.execute(
            "players", "SELECT discordid FROM players WHERE playername = ?", (player2,)
        ).fetchone()

        # Отправляем уведомления
        try:
            if player1_data:
                user1 = await global_bot.fetch_user(int(player1_data[0]))
                await user1.send(f"ℹ️ Результат матча #{self.match_id} {action}.")

            if player2_data:
                user2 = await global_bot.fetch_user(int(player2_data[0]))
                await user2.send(f"ℹ️ Результат матча #{self.match_id} {action}.")
        except Exception as e:
            print(f"Ошибка уведомления игроков: {e}")


class PlayerConfirmationView(View):
    def __init__(self, match_id, submitter_id, opponent_id):
        super().__init__(timeout=3600)  # Таймаут 1 час (3600 секунд)
        self.match_id = match_id
        self.submitter_id = submitter_id
        self.opponent_id = opponent_id

    @discord.ui.button(label="Подтвердить", style=discord.ButtonStyle.green)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message(
                "❌ Это не ваш матч!", ephemeral=True
            )
            return

        # Получаем данные результата
        result_data = pending_player_confirmations.get(self.match_id)
        if not result_data:
            await interaction.response.send_message(
                "❌ Данные о результате устарели", ephemeral=True
            )
            return

        # Удаляем из ожидания
        if self.match_id in pending_player_confirmations:
            del pending_player_confirmations[self.match_id]

        # Обрабатываем результат
        await self.process_match_result(result_data)
        await interaction.response.send_message(
            "✅ Результат подтвержден!", ephemeral=True
        )
        await interaction.message.delete()

    @discord.ui.button(label="Оспорить", style=discord.ButtonStyle.red)
    async def dispute(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message(
                "❌ Это не ваш матч!", ephemeral=True
            )
            return

        # Получаем данные результата
        result_data = pending_player_confirmations.get(self.match_id)
        if not result_data:
            await interaction.response.send_message(
                "❌ Данные о результате устарели", ephemeral=True
            )
            return

        # Отправляем модератору
        await self.send_to_moderator(result_data)

        await interaction.response.send_message(
            "✅ Результат оспорен! Модератор рассмотрит спор.", ephemeral=True
        )
        await interaction.message.delete()

    async def process_match_result(self, result_data):
        """Обработка подтвержденного результата"""
        try:
            match_id = result_data["match_id"]
            scores = result_data["scores"]
            player1 = result_data["player1"]
            player2 = result_data["player2"]
            mode = result_data["mode"]

            # Получаем тип матча
            matchtype = db_manager.fetchone(
                "matches",
                "SELECT matchtype FROM matches WHERE matchid = ?",
                (match_id,),
            )[0]
            # Парсим счет
            score1, score2 = map(int, scores.split("-"))

            # Определяем результат
            if score1 > score2:
                winner = player1
                loser = player2
            else:
                winner = player2
                loser = player1

            # Получаем текущие рейтинги
            rating_winner = get_player_rating(winner, mode)
            rating_loser = get_player_rating(loser, mode)

            # Рассчитываем новые рейтинги
            new_rating_winner, new_rating_loser = calculate_elo(
                rating_winner, rating_loser, 1
            )

            # Обновляем статистику
            db_manager.execute(
                "players",
                "UPDATE players SET wins = wins + 1 WHERE playername = ?",
                (winner,),
            )
            db_manager.execute(
                "players",
                "UPDATE players SET losses = losses + 1 WHERE playername = ?",
                (loser,),
            )

            # Обновляем ELO только для обычных матчей
            if matchtype == 1:
                rating_winner = get_player_rating(winner, mode)
                rating_loser = get_player_rating(loser, mode)
                new_rating_winner, new_rating_loser = calculate_elo(rating_winner, rating_loser, 1)
                update_player_rating(winner, new_rating_winner, mode)
                update_player_rating(loser, new_rating_loser, mode)
                elo_change = f"\n\n**Изменения ELO:**\n{winner}: {rating_winner} → **{new_rating_winner}**\n{loser}: {rating_loser} → **{new_rating_loser}**"
            else:
                elo_change = ""

            # Обновляем запись матча
            db_manager.execute(
                "matches",
                "UPDATE matches SET player1score = ?, player2score = ?, isover = 1, isverified = 1 WHERE matchid = ?",
                (score1, score2, match_id),
            )

            # Отправляем результат в канал
            mode_name = MODE_NAMES.get(mode, "Unknown")
            embed = discord.Embed(
                title=f"🏁 Матч завершен | ID: {match_id}",
                description=(
                    f"**Режим:** {mode_name}\n"
                    f"**Игроки:** {player1} vs {player2}\n"
                    f"**Счет:** {score1} - {score2}\n"
                    f"**Победитель:** {winner}"
                    f"{elo_change}"
                ),
                color=discord.Color.green(),
            )

            # Ищем канал результатов
            results_channel = None
            for guild in global_bot.guilds:
                for channel in guild.text_channels:
                    if channel.name == "elobot-results":
                        results_channel = channel
                        break
                if results_channel:
                    break

            if results_channel:
                await results_channel.send(embed=embed)
            else:
                print("⚠ Канал elobot-results не найден")

            # Отправляем результат игрокам в ЛС
            try:
                submitter_user = await global_bot.fetch_user(
                    result_data["submitter_id"]
                )
                opponent_user = await global_bot.fetch_user(result_data["opponent_id"])

                # Создаем персонализированные сообщения
                await submitter_user.send(
                    f"✅ Ваш оппонент подтвердил результат матча #{match_id}"
                )
                await submitter_user.send(embed=embed)

                await opponent_user.send(
                    f"✅ Вы подтвердили результат матча #{match_id}"
                )
                await opponent_user.send(embed=embed)
            except Exception as e:
                print(f"Ошибка отправки результата игрокам: {e}")

        except Exception as e:
            print(f"Ошибка обработки результата: {e}")
            # Уведомляем модератора об ошибке
            try:
                moderator = await global_bot.fetch_user(MODERATOR_ID)
                await moderator.send(
                    f"❌ Ошибка обработки результата матча #{match_id}: {str(e)}"
                )
            except:
                pass

    async def send_to_moderator(self, result_data):
        """Отправка оспоренного результата модератору"""
        try:
            moderator = await global_bot.fetch_user(MODERATOR_ID)

            embed = discord.Embed(
                title="⚠️ Оспоренный результат матча",
                description=(
                    f"**Match ID:** {result_data['match_id']}\n"
                    f"**Игроки:** {result_data['player1']} vs {result_data['player2']}\n"
                    f"**Счет:** {result_data['scores']}\n"
                    f"**Отправил:** <@{result_data['submitter_id']}>\n"
                    f"**Оспорил:** <@{result_data['opponent_id']}>"
                ),
                color=discord.Color.orange(),
            )

            if result_data["screenshot"]:
                embed.set_image(url=result_data["screenshot"])

            view = ModeratorResolutionView(result_data["match_id"])
            await moderator.send(embed=embed, view=view)

        except Exception as e:
            print(f"Ошибка отправки модератору: {e}")

    async def on_timeout(self):
        """Отправляем модератору при таймауте (1 час)"""
        result_data = pending_player_confirmations.get(self.match_id)
        if result_data:
            # Уведомляем игроков о таймауте
            try:
                submitter_user = await global_bot.fetch_user(
                    result_data["submitter_id"]
                )
                opponent_user = await global_bot.fetch_user(result_data["opponent_id"])

                await submitter_user.send(
                    f"⌛ Ваш оппонент не подтвердил результат матча #{self.match_id} в течение часа. "
                    f"Результат отправлен модератору на проверку."
                )
                await opponent_user.send(
                    f"⌛ Вы не подтвердили результат матча #{self.match_id} в течение часа. "
                    f"Результат отправлен модератору на проверку."
                )
            except:
                pass

            # Отправляем модератору
            await self.send_to_moderator(result_data)

        try:
            await self.message.delete()
        except:
            pass


def save_queues_to_db():
    """Сохраняет текущее состояние очередей в БД"""
    try:
        # Сначала сбрасываем все флаги
        db_manager.execute("players", "UPDATE players SET in_queue = 0")

        # Устанавливаем флаги для игроков в очередях
        for mode, queue in queues.items():
            for player in queue:
                db_manager.execute(
                    "players",
                    "UPDATE players SET in_queue = 1 WHERE discordid = ?",
                    (str(player["discord_id"]),),
                )
    except Exception as e:
        print(f"Ошибка сохранения очередей в БД: {e}")


class ReportView(View):
    def __init__(self, match_id, reporter_name, violator_name):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.reporter_name = reporter_name
        self.violator_name = violator_name

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.danger)
    async def accept_report(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Получаем данные матча
        match_data = db_manager.execute(
            "matches",
            """
            SELECT mode, player1, player2, isverified, player1score, player2score 
            FROM matches 
            WHERE matchid = ?
            """,
            (self.match_id,),
        ).fetchone()

        if not match_data:
            await interaction.response.send_message("❌ Матч не найден", ephemeral=True)
            return

        mode, player1, player2, isverified, p1_score, p2_score = match_data

        # Если результат уже был подтвержден - отменяем его
        if isverified == 1:
            # Определяем предыдущих победителя и проигравшего
            if p1_score > p2_score:
                winner_old = player1
                loser_old = player2
            else:
                winner_old = player2
                loser_old = player1

            # Откатываем статистику
            db_manager.execute(
                "players",
                "UPDATE players SET wins = wins - 1 WHERE playername = ?",
                (winner_old,),
            )
            db_manager.execute(
                "players",
                "UPDATE players SET losses = losses - 1 WHERE playername = ?",
                (loser_old,),
            )

            # Откатываем ELO (сохраняем для отчета)
            old_winner_rating = get_player_rating(winner_old, mode)
            old_loser_rating = get_player_rating(loser_old, mode)

        # Применяем техническое поражение
        winner = self.reporter_name
        loser = self.violator_name

        # Определяем счет
        if player1 == winner:
            new_p1_score = 1
            new_p2_score = 0
        else:
            new_p1_score = 0
            new_p2_score = 1

        # Получаем текущие рейтинги для отчета
        winner_rating = get_player_rating(winner, mode)
        loser_rating = get_player_rating(loser, mode)

        # Рассчитываем новые рейтинги
        new_winner_rating, new_loser_rating = calculate_elo(
            winner_rating, loser_rating, 1
        )

        # Обновляем статистику
        db_manager.execute(
            "players",
            "UPDATE players SET wins = wins + 1 WHERE playername = ?",
            (winner,),
        )
        db_manager.execute(
            "players",
            "UPDATE players SET losses = losses + 1 WHERE playername = ?",
            (loser,),
        )

        # Обновляем ELO
        update_player_rating(winner, new_winner_rating, mode)
        update_player_rating(loser, new_loser_rating, mode)

        # Обновляем матч
        db_manager.execute(
            "matches",
            """
            UPDATE matches 
            SET player1score = ?, player2score = ?, isover = 1, isverified = 1 
            WHERE matchid = ?
            """,
            (new_p1_score, new_p2_score, self.match_id),
        )

        # Отправляем результат в канал
        moderator_name = f"{interaction.user.name}#{interaction.user.discriminator}"
        await self.send_report_result(
            mode,
            winner,
            loser,
            winner_rating,  # старый рейтинг победителя
            loser_rating,  # старый рейтинг проигравшего
            new_winner_rating,
            new_loser_rating,
            moderator_name,
        )

        # Уведомляем игроков
        try:
            # Получаем discord_id игроков
            winner_row = db_manager.execute(
                "players",
                "SELECT discordid FROM players WHERE playername = ?",
                (winner,),
            ).fetchone()
            if winner_row:
                winner_id = int(winner_row[0])
                winner_user = await global_bot.fetch_user(winner_id)
                await winner_user.send(
                    f"✅ Ваш репорт на матч #{self.match_id} принят. "
                    f"Противнику назначено техническое поражение."
                )

            loser_row = db_manager.execute(
                "players",
                "SELECT discordid FROM players WHERE playername = ?",
                (loser,),
            ).fetchone()
            if loser_row:
                loser_id = int(loser_row[0])
                loser_user = await global_bot.fetch_user(loser_id)
                await loser_user.send(
                    f"⚠️ Вам назначено техническое поражение по матчу #{self.match_id} "
                    f"из-за принятого репорта."
                )
        except Exception as e:
            print(f"Ошибка уведомления игроков: {e}")

        # Удаляем репорт из ожидания
        if self.match_id in pending_reports:
            del pending_reports[self.match_id]

        await interaction.response.send_message(
            "✅ Репорт принят. Техническое поражение применено.", ephemeral=True
        )
        await interaction.message.edit(view=None)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.secondary)
    async def reject_report(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Возвращаем матч в предыдущее состояние
        db_manager.execute(
            "matches",
            "UPDATE matches SET isover = 0 WHERE matchid = ?",
            (self.match_id,),
        )

        # Уведомляем репортера
        try:
            reporter_id = pending_reports[self.match_id]["reporter_id"]
            reporter_user = await global_bot.fetch_user(reporter_id)
            await reporter_user.send(
                f"❌ Ваш репорт на матч #{self.match_id} отклонен."
            )
        except:
            pass

        # Удаляем репорт из ожидания
        if self.match_id in pending_reports:
            del pending_reports[self.match_id]

        await interaction.response.send_message("❌ Репорт отклонен.", ephemeral=True)
        await interaction.message.edit(view=None)

    async def send_report_result(
        self,
        mode,
        winner,
        loser,
        old_winner_rating,
        old_loser_rating,
        new_winner_rating,
        new_loser_rating,
        moderator_name,
    ):
        """Отправляет результат репорта в канал результатов"""
        try:
            mode_name = MODE_NAMES.get(mode, "Unknown")

            # Рассчитываем изменения ELO
            winner_change = new_winner_rating - old_winner_rating
            loser_change = new_loser_rating - old_loser_rating

            # Создаем embed
            embed = discord.Embed(
                title="⚠️ Матч завершен (техническое поражение)",
                description=(
                    f"**Match ID:** {self.match_id}\n"
                    f"**Режим:** {mode_name}\n"
                    f"**Победитель:** {winner}\n"
                    f"**Проигравший:** {loser}\n\n"
                    f"**Решение принял:** {moderator_name}\n\n"
                    f"**Изменения ELO:**\n"
                    f"{winner}: {old_winner_rating} → **{new_winner_rating}** ({winner_change:+})\n"
                    f"{loser}: {old_loser_rating} → **{new_loser_rating}** ({loser_change:+})"
                ),
                color=discord.Color.red(),
            )
            embed.set_footer(text=f"Техническое поражение по репорту")

            # Ищем канал результатов
            results_channel = None
            for guild in global_bot.guilds:
                for channel in guild.text_channels:
                    if channel.name == "elobot-results":
                        results_channel = channel
                        break
                if results_channel:
                    break

            if results_channel:
                await results_channel.send(embed=embed)
            else:
                print("⚠ Канал elobot-results не найден")
        except Exception as e:
            print(f"Ошибка отправки результата репорта: {e}")


global_bot = None
# Очереди для каждого режима
queues = {mode: [] for mode in MODES.values()}

# Глобальные переменные для отслеживания процессов
map_voting = (
    {}
)  # {match_id: {"players": [p1, p2], "remaining_maps": [...], "current_player": discord_id}}

pending_results = (
    {}
)  # {message_id: {"match_id": int, "player1": str, "player2": str, "scores": str, "screenshot": str}}


def get_discord_id_by_nickname(nickname):
    result = db_manager.execute(
        "players",
        "SELECT discordid FROM players WHERE playername = ?",
        (nickname,),
    ).fetchone()
    return int(result[0]) if result else None


# Объявляем функцию send_map_selection перед использованием
async def send_map_selection(match_id):
    global global_bot, map_voting
    if match_id not in map_voting:
        return

    voting = map_voting[match_id]
    current_player = voting["current_player"]
    remaining_maps = voting["remaining_maps"]

    # Удаляем предыдущее сообщение если есть
    if current_player in voting.get("messages", {}):
        try:
            await voting["messages"][current_player].delete()
        except:
            pass

    try:
        # Создаем новое View
        view = MapSelectionView(match_id, remaining_maps, current_player)

        # Отправляем новое сообщение
        msg = await global_bot.get_user(current_player).send(
            f"**Ваш ход!** Выберите карту для вычеркивания:", view=view
        )

        # Сохраняем ссылку на сообщение
        voting.setdefault("messages", {})[current_player] = msg
        view.message = msg  # Для доступа в on_timeout

        # Уведомляем другого игрока
        other_player = next(pid for pid in voting["players"] if pid != current_player)
        try:
            await global_bot.get_user(other_player).send(
                f"Ожидайте своего хода. Сейчас выбирает <@{current_player}>."
            )
        except:
            pass

    except Exception as e:
        print(f"Ошибка отправки выбора карт: {e}")
        # Если не удалось отправить - пропускаем ход
        voting["current_player"] = other_player
        await asyncio.sleep(1)
        await send_map_selection(match_id)


class MapSelectionView(View):
    def __init__(self, match_id, maps, player_id):
        super().__init__(timeout=120)
        self.match_id = match_id
        self.player_id = player_id
        self.maps = maps
        self.has_responded = False  # Флаг для отслеживания ответа

        for map_name in maps:
            button = Button(
                label=map_name,
                style=discord.ButtonStyle.secondary,
                custom_id=f"map_{map_name}",
            )
            button.callback = lambda i, m=map_name: self.button_callback(i, m)
            self.add_item(button)

    async def on_timeout(self):
        """Автоматически выбирает случайную карту при таймауте"""
        if self.has_responded:
            return

        self.has_responded = True
        global map_voting

        if self.match_id not in map_voting:
            return

        voting = map_voting[self.match_id]

        # Если карты закончились - завершаем
        if not voting["remaining_maps"]:
            await self.finish_map_selection()
            return

        # Выбираем случайную карту
        selected_map = random.choice(voting["remaining_maps"])

        try:
            # Обновляем сообщение
            await self.message.edit(
                content=f"⏱ Время вышло! Автоматически вычеркнута карта **{selected_map}**",
                view=None,
            )
        except discord.NotFound:
            pass  # Сообщение уже удалено

        # Обновляем список карт
        if selected_map in voting["remaining_maps"]:
            voting["remaining_maps"].remove(selected_map)
        voting["last_selected"] = selected_map

        # Проверяем завершение процесса
        if len(voting["remaining_maps"]) <= 1:
            await self.finish_map_selection()
            return

        # Передаем ход следующему игроку
        voting["current_player"] = (
            voting["players"][1]
            if voting["current_player"] == voting["players"][0]
            else voting["players"][0]
        )

        # Запускаем выбор для следующего игрока
        await send_map_selection(self.match_id)

    async def button_callback(self, interaction: discord.Interaction, map_name: str):
        """Обработчик выбора карты"""
        if self.has_responded:
            await interaction.response.send_message(
                "⌛ Это взаимодействие больше не активно", ephemeral=True
            )
            return

        self.has_responded = True
        global map_voting

        if self.match_id not in map_voting:
            await interaction.response.send_message(
                "Процесс выбора карты завершен", ephemeral=True
            )
            return

        voting = map_voting[self.match_id]
        selected_map = map_name

        # Удаляем выбранную карту
        if selected_map in voting["remaining_maps"]:
            voting["remaining_maps"].remove(selected_map)
        voting["last_selected"] = selected_map

        # Если осталась только одна карта - завершаем процесс
        if len(voting["remaining_maps"]) <= 1:
            await interaction.response.edit_message(
                content=f"Вы вычеркнули карту **{selected_map}**", view=None
            )
            await self.finish_map_selection()
            return

        # Переключаем ход
        voting["current_player"] = (
            voting["players"][1]
            if voting["current_player"] == voting["players"][0]
            else voting["players"][0]
        )

        await interaction.response.edit_message(
            content=f"Вы вычеркнули карту **{selected_map}**", view=None
        )
        await send_map_selection(self.match_id)

    async def finish_map_selection(self):
        global map_voting
        if self.match_id not in map_voting:
            print(f"ОШИБКА: Данные матча {self.match_id} потеряны!")
            return

        voting = map_voting.get(self.match_id)
        if not voting or not voting.get("remaining_maps"):
            print(f"ОШИБКА: Нет данных о картах для матча {self.match_id}")
            return
        selected_map = (
            voting["remaining_maps"][0] if voting["remaining_maps"] else "Станция"
        )

        # Сохраняем выбранную карту в базе данных
        db_manager.execute(
            "matches",
            "UPDATE matches SET map = ? WHERE matchid = ?",
            (selected_map, self.match_id),
        )

        # Отправляем результат обоим игрокам
        for player_id in voting["players"]:
            # Определяем соперника
            opponent_id = next(pid for pid in voting["players"] if pid != player_id)

            # Безопасное получение никнейма
            if (
                "player_nicknames" in voting
                and opponent_id in voting["player_nicknames"]
            ):
                opponent_nickname = voting["player_nicknames"][opponent_id]
            else:
                # Если нет в словаре, попробуем получить из БД
                try:
                    # Исправленная строка: правильный синтаксис параметра
                    player_data = db_manager.execute(
                        "players",
                        "SELECT playername FROM players WHERE discordid = ?",
                        (str(opponent_id),),  # <-- Запятая внутри кортежа
                    ).fetchone()
                    opponent_nickname = (
                        player_data[0] if player_data else "Неизвестный игрок"
                    )
                except Exception as e:
                    print(f"Ошибка при получении никнейма из БД: {e}")
                    opponent_nickname = "Неизвестный игрок"

            try:
                # Получаем информацию о сопернике
                opponent_user = await global_bot.fetch_user(opponent_id)
                discord_tag = f"{opponent_user.name}#{opponent_user.discriminator}"
            except:
                discord_tag = "неизвестен"

            # Создаем embed
            embed = discord.Embed(
                title="Черкание завершено", color=discord.Color.green()
            )
            embed.add_field(name="Карта", value=f"**{selected_map}**", inline=False)
            embed.add_field(
                name="Противник", value=f"**{opponent_nickname}**", inline=False
            )
            embed.add_field(name="Discord противника", value=discord_tag, inline=False)
            embed.set_footer(text=f"Match ID: {self.match_id}")

            try:
                await global_bot.get_user(player_id).send(embed=embed)
            except Exception as e:
                print(f"Ошибка при отправке сообщения игроку {player_id}: {e}")

        # Отправляем инструкцию обоим игрокам
        try:
            player1 = await global_bot.fetch_user(voting["players"][0])
            player2 = await global_bot.fetch_user(voting["players"][1])

            instruction = (
                "🔍 Найдите вашего противника в Discord и договоритесь о создании игры.\n"
                "ℹ️ После завершения матча **победитель** должен отправить результат командой "
                "`.result <ID_матча> <свой_счет>-<счет_соперника>` в личные сообщения боту, "
                "приложив скриншот.\n"
                "Пример: `.result 123 5-3`"
            )

            await player1.send(instruction)
            await player2.send(instruction)
        except Exception as e:
            print(f"Ошибка при отправке инструкции: {e}")

        # Удаляем данные о голосовании
        if self.match_id in map_voting:
            del map_voting[self.match_id]


class ModeSelectView(View):
    def __init__(self, player_id):
        super().__init__(timeout=30)
        self.player_id = player_id
        self.selected_mode = None

        options = [
            discord.SelectOption(label="Any", value="0"),
            discord.SelectOption(label="Station 5 flags", value="1"),
            discord.SelectOption(label="MotS Solo", value="2"),
            discord.SelectOption(label="12min", value="3"),
        ]

        self.select = Select(placeholder="Выберите режим игры", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message(
                "Это не ваша очередь!", ephemeral=True
            )
            return

        self.selected_mode = int(self.select.values[0])
        await interaction.response.defer()
        self.stop()


def calculate_elo(player1_rating, player2_rating, result, K=40, C=400, max_rating=4000):
    expected_1 = 1 / (1 + 10 ** ((player2_rating - player1_rating) / C))
    expected_2 = 1 - expected_1

    weight_1 = max_rating / (max_rating + player1_rating)
    weight_2 = max_rating / (max_rating + player2_rating)

    new_rating_1 = player1_rating + K * (result - expected_1) * weight_1
    new_rating_2 = player2_rating + K * ((1 - result) - expected_2) * weight_2

    return round(new_rating_1), round(new_rating_2)


def get_player_rating(nickname, mode):
    if mode == MODES["station5f"]:
        rating = db_manager.execute(
            "players",
            "SELECT elo_station5f FROM players WHERE playername = ?",
            (nickname,),
        ).fetchone()
    elif mode == MODES["mots"]:
        rating = db_manager.execute(
            "players", "SELECT elo_mots FROM players WHERE playername = ?", (nickname,)
        ).fetchone()
    elif mode == MODES["12min"]:
        rating = db_manager.execute(
            "players", "SELECT elo_12min FROM players WHERE playername = ?", (nickname,)
        ).fetchone()
    else:
        rating = db_manager.execute(
            "players",
            "SELECT currentelo FROM players WHERE playername = ?",
            (nickname,),
        ).fetchone()

    return rating[0] if rating else 1000


def update_player_rating(nickname, new_rating, mode):
    # Обновляем ELO для конкретного режима
    if mode == MODES["station5f"]:
        db_manager.execute(
            "players",
            "UPDATE players SET elo_station5f = ? WHERE playername = ?",
            (new_rating, nickname),
        )
    elif mode == MODES["mots"]:
        db_manager.execute(
            "players",
            "UPDATE players SET elo_mots = ? WHERE playername = ?",
            (new_rating, nickname),
        )
    elif mode == MODES["12min"]:
        db_manager.execute(
            "players",
            "UPDATE players SET elo_12min = ? WHERE playername = ?",
            (new_rating, nickname),
        )

    # Обновляем суммарный ELO
    db_manager.execute(
        "players",
        """
        UPDATE players 
        SET currentelo = elo_station5f + elo_mots + elo_12min 
        WHERE playername = ?
        """,
        (nickname,),
    )


async def find_match():
    """Поиск подходящих матчей в очередях с учетом типа матча"""
    while True:
        await asyncio.sleep(15)
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] Проверка очередей: {[len(q) for q in queues.values()]}"
        )

        try:
            # Получаем списки игроков в активных матчах по типам
            active_players = {1: set(), 2: set()}  # Обычные матчи  # Турнирные матчи

            # Заполняем множества активных игроков для каждого типа матча
            for match_type in [1, 2]:
                matches = db_manager.fetchall(
                    "matches",
                    "SELECT player1, player2 FROM matches WHERE isover = 0 AND matchtype = ?",
                    (match_type,),
                )
                for player1, player2 in matches:
                    active_players[match_type].add(player1)
                    active_players[match_type].add(player2)

            # Обработка стандартных режимов (1, 2, 3) - только обычные матчи (matchtype=1)
            for mode in [MODES["station5f"], MODES["mots"], MODES["12min"]]:
                # Фильтруем игроков, исключая тех, кто уже в обычном матче
                queue = [
                    p for p in queues[mode] if p["nickname"] not in active_players[1]
                ]

                if len(queue) >= 2:
                    try:
                        # Сортируем по времени в очереди
                        queue.sort(key=lambda x: x["join_time"])

                        # Берем первого игрока в очереди
                        player1 = queue.pop(0)

                        # Ищем наиболее подходящего соперника по рейтингу
                        min_diff = float("inf")
                        candidate = None
                        candidate_idx = None

                        for idx, p in enumerate(queue):
                            diff = abs(player1["rating"] - p["rating"])
                            if diff < min_diff:
                                min_diff = diff
                                candidate = p
                                candidate_idx = idx

                        if candidate_idx is not None:
                            player2 = queue.pop(candidate_idx)
                            await create_match(mode, player1, player2)
                            save_queues_to_db()
                    except Exception as e:
                        print(f"Ошибка обработки очереди {MODE_NAMES[mode]}: {e}")
                        continue

            # Обработка режима "Any" (0)
            queue_any = [
                p
                for p in queues[MODES["any"]]
                if p["nickname"] not in active_players[1]
            ]

            if queue_any:
                try:
                    # Поиск в других режимах (1, 2, 3)
                    min_diff = float("inf")
                    candidate = None
                    candidate_mode = None
                    candidate_idx = None

                    for mode in [MODES["station5f"], MODES["mots"], MODES["12min"]]:
                        queue = [
                            p
                            for p in queues[mode]
                            if p["nickname"] not in active_players[1]
                        ]

                        for idx, p in enumerate(queue):
                            diff = abs(queue_any[0]["rating"] - p["rating"])
                            if diff < min_diff:
                                min_diff = diff
                                candidate = p
                                candidate_mode = mode
                                candidate_idx = idx

                    if candidate:
                        player_any = queue_any.pop(0)
                        queues[candidate_mode].pop(candidate_idx)
                        await create_match(candidate_mode, player_any, candidate)
                        save_queues_to_db()
                    else:
                        # Поиск внутри очереди "Any"
                        if len(queue_any) >= 2:
                            player1 = queue_any.pop(0)
                            min_diff = float("inf")
                            candidate = None
                            candidate_idx = None

                            for idx, p in enumerate(queue_any):
                                diff = abs(player1["rating"] - p["rating"])
                                if diff < min_diff:
                                    min_diff = diff
                                    candidate = p
                                    candidate_idx = idx

                            if candidate_idx is not None:
                                player2 = queue_any.pop(candidate_idx)
                                random_mode = random.choice(
                                    [MODES["station5f"], MODES["mots"], MODES["12min"]]
                                )
                                await create_match(random_mode, player1, player2)
                                save_queues_to_db()
                except Exception as e:
                    print(f"Ошибка обработки очереди Any: {e}")
                    continue

        except Exception as e:
            print(f"Критическая ошибка в find_match: {e}")
            # Сохраняем состояние даже при ошибке
            save_queues_to_db()


async def create_match(mode, player1, player2, matchtype=1, tournament_id=None):
    """Создает матч и уведомляет игроков"""
    try:
        print(
            f"[MATCH] Создание матча: {player1['nickname']} vs {player2['nickname']} ({MODE_NAMES[mode]})"
        )

        # Проверяем, является ли один из игроков "emptyslot"
        is_player1_empty = player1["nickname"].startswith("emptyslot")
        is_player2_empty = player2["nickname"].startswith("emptyslot")

        # Если оба "emptyslot", матч не создаем (турнирная логика обработает это отдельно)
        if is_player1_empty and is_player2_empty:
            print(f"[MATCH] Оба игрока — emptyslot, матч не создается")
            return None

        # Обновляем статус в базе только для реальных игроков
        if not is_player1_empty and not is_player2_empty:
            db_manager.execute(
                "players",
                "UPDATE players SET in_queue = 0 WHERE playername IN (?, ?)",
                (player1["nickname"], player2["nickname"]),
            )

        # Создаем запись о матче и получаем ID
        cursor = db_manager.execute(
            "matches",
            """
            INSERT INTO matches (mode, player1, player2, start_time, matchtype, tournament_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mode,
                player1["nickname"],
                player2["nickname"],
                datetime.now(),
                matchtype,
                tournament_id,
            ),
        )
        match_id = cursor.lastrowid

        # Если один из игроков — "emptyslot", автоматически завершаем матч
        if matchtype == 2 and (is_player1_empty or is_player2_empty):
            winner = player2["nickname"] if is_player1_empty else player1["nickname"]
            db_manager.execute(
                "matches",
                """
                UPDATE matches 
                SET player1score = ?, player2score = ?, isover = 1, isverified = 1
                WHERE matchid = ?
                """,
                (0 if is_player1_empty else 1, 1 if is_player1_empty else 0, match_id),
            )
            print(f"[MATCH] Матч с emptyslot автоматически завершен в пользу {winner}")
            return match_id

        # Обновляем статус в базе
        db_manager.execute(
            "players",
            "UPDATE players SET in_queue = 0 WHERE playername IN (?, ?)",
            (player1["nickname"], player2["nickname"]),
        )

        # Создаем запись о матче и получаем ID
        cursor = db_manager.execute(
            "matches",
            """
            INSERT INTO matches (mode, player1, player2, start_time, matchtype, tournament_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                mode,
                player1["nickname"],
                player2["nickname"],
                datetime.now(),
                matchtype,
                tournament_id,
            ),
        )
        match_id = cursor.lastrowid

        # Уведомляем в канале очереди
        try:
            channel = global_bot.get_channel(player1["channel_id"])
            mode_name = MODE_NAMES.get(mode, "Unknown")

            embed = discord.Embed(
                title="🎮 Матч найден!",
                description=(
                    f"**Режим:** {mode_name}\n"
                    f"**Match ID:** {match_id}\n"
                    f"**Тип:** {'Турнирный' if matchtype == 2 else 'Обычный'}\n"
                    f"**Игрок 1:** {player1['nickname']}\n"
                    f"**Игрок 2:** {player2['nickname']}"
                ),
                color=discord.Color.green(),
            )
            await channel.send(embed=embed)
        except Exception as e:
            print(f"Ошибка уведомления в канале: {e}")

        # Личные сообщения игрокам
        for player_data, opponent_data in [(player1, player2), (player2, player1)]:
            try:
                user = await global_bot.fetch_user(player_data["discord_id"])
                opponent_user = await global_bot.fetch_user(opponent_data["discord_id"])

                # Форматируем тэг соперника
                discord_tag = f"{opponent_user.name}#{opponent_user.discriminator}"

                embed = discord.Embed(
                    title="🎮 Матч найден!", color=discord.Color.green()
                )
                embed.add_field(name="Режим", value=f"**{mode_name}**", inline=False)
                embed.add_field(
                    name="Тип матча",
                    value="Турнирный" if matchtype == 2 else "Обычный",
                    inline=False,
                )
                embed.add_field(
                    name="Противник",
                    value=f"**{opponent_data['nickname']}**",
                    inline=False,
                )
                embed.add_field(
                    name="Discord противника", value=discord_tag, inline=False
                )
                embed.set_footer(text=f"Match ID: {match_id}")

                instruction = (
                    "🔍 Найдите вашего противника в Discord и договоритесь о создании игры.\n"
                    f"**Discord противника:** {discord_tag}\n\n"
                    "ℹ️ После завершения матча **победитель** должен отправить результат командой "
                    "`.result <ID_матча> <свой_счет>-<счет_соперника>` в личные сообщения боту, "
                    "приложив скриншот.\n"
                    "Пример: `.result {match_id} 5-3`"
                )

                await user.send(embed=embed)
                await user.send(instruction)
            except Exception as e:
                print(f"Ошибка отправки ЛС игроку: {e}")

        # Для MotS и 12min инициируем черкание карт
        if mode in [MODES["mots"], MODES["12min"]]:
            map_voting[match_id] = {
                "players": [player1["discord_id"], player2["discord_id"]],
                "player_nicknames": {
                    player1["discord_id"]: player1["nickname"],
                    player2["discord_id"]: player2["nickname"],
                },
                "remaining_maps": MAPS.copy(),
                "current_player": player1["discord_id"],
                "messages": {},
                "mode": mode,
            }
            await send_map_selection(match_id)

    except Exception as e:
        print(f"Ошибка создания матча: {e}")
        # Возвращаем игроков в очередь при ошибке
        queues[mode].append(player1)
        queues[mode].append(player2)
        save_queues_to_db()
    finally:
        # Всегда сохраняем состояние после попытки создания матча
        save_queues_to_db()


# Фоновая задача для периодического сохранения
async def periodic_queue_saver():
    """Периодически сохраняет состояние очередей"""
    while True:
        await asyncio.sleep(600)  # Каждые 10 минут
        try:
            save_queues_to_db()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Автосохранение очередей")
        except Exception as e:
            print(f"Ошибка автосохранения: {e}")


async def check_expired_matches(bot):
    await bot.wait_until_ready()
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Запущена задача проверки просроченных матчей"
    )

    while not bot.is_closed():
        try:
            # Проверяем каждые 5 минут
            await asyncio.sleep(300)

            now = datetime.now()
            one_hour_ago = now - timedelta(hours=1)
            
            print(
                f"[{now.strftime('%H:%M:%S')}] Проверка матчей старше {one_hour_ago.strftime('%H:%M:%S')}"
            )

            # Исключаем турнирные матчи (matchtype=2)
            expired_matches = db_manager.execute(
                "matches",
                """
                SELECT matchid, mode, player1, player2, start_time 
                FROM matches 
                WHERE isover = 0 AND start_time < ? AND matchtype = 1
                """,
                (one_hour_ago,),
            ).fetchall()

            print(f"Найдено {len(expired_matches)} просроченных матчей")

            for match in expired_matches:
                match_id, mode, player1_name, player2_name, start_time = match
                print(
                    f"Обработка матча {match_id}: {player1_name} vs {player2_name} (начат в {start_time})"
                )

                # Двойная проверка статуса матча
                match_status = db_manager.execute(
                    "matches",
                    "SELECT isover FROM matches WHERE matchid = ?",
                    (match_id,),
                ).fetchone()

                if match_status and match_status[0] == 1:
                    print(f"Матч {match_id} уже завершен, пропускаем")
                    continue

                print(f"Матч {match_id} просрочен, завершаем автоматически")

                # Обновляем матч как ничью
                db_manager.execute(
                    "matches",
                    "UPDATE matches SET player1score = 0, player2score = 0, isover = 1, isverified = 1 WHERE matchid = ?",
                    (match_id,),
                )
                print(f"Матч {match_id} помечен как завершенный (ничья)")

                # Обновляем статистику игроков
                try:
                    rating1 = get_player_rating(player1_name, mode)
                    rating2 = get_player_rating(player2_name, mode)
                    new_rating1, new_rating2 = calculate_elo(
                        rating1, rating2, 0.5
                    )  # Ничья

                    update_player_rating(player1_name, new_rating1, mode)
                    update_player_rating(player2_name, new_rating2, mode)

                    # Обновляем счетчики ничьих
                    if mode == MODES["station5f"]:
                        db_manager.execute(
                            "players",
                            "UPDATE players SET ties_station5f = ties_station5f + 1 WHERE playername = ?",
                            (player1_name,),
                        )
                        db_manager.execute(
                            "players",
                            "UPDATE players SET ties_station5f = ties_station5f + 1 WHERE playername = ?",
                            (player2_name,),
                        )
                    elif mode == MODES["mots"]:
                        db_manager.execute(
                            "players",
                            "UPDATE players SET ties_mots = ties_mots + 1 WHERE playername = ?",
                            (player1_name,),
                        )
                        db_manager.execute(
                            "players",
                            "UPDATE players SET ties_mots = ties_mots + 1 WHERE playername = ?",
                            (player2_name,),
                        )
                    elif mode == MODES["12min"]:
                        db_manager.execute(
                            "players",
                            "UPDATE players SET ties_12min = ties_12min + 1 WHERE playername = ?",
                            (player1_name,),
                        )
                        db_manager.execute(
                            "players",
                            "UPDATE players SET ties_12min = ties_12min + 1 WHERE playername = ?",
                            (player2_name,),
                        )

                    db_manager.execute(
                        "players",
                        "UPDATE players SET ties = ties + 1 WHERE playername IN (?, ?)",
                        (player1_name, player2_name),
                    )
                    print("Статистика игроков обновлена")
                except Exception as e:
                    print(f"Ошибка при обновлении статистики: {e}")

                # Уведомление игроков в ЛС
                try:
                    user1_id = get_discord_id_by_nickname(player1_name)
                    user2_id = get_discord_id_by_nickname(player2_name)

                    # Создаем embed один раз
                    embed_dm = discord.Embed(
                        title="⏱ Матч завершен автоматически",
                        description=(
                            f"Матч #{match_id} между **{player1_name}** и **{player2_name}**\n"
                            f"Режим: **{MODE_NAMES.get(mode, 'Unknown')}**\n"
                            f"Был автоматически завершен вничью, так как превышено время (1 час).\n\n"
                            f"**Изменения ELO:**\n"
                            f"{player1_name}: {rating1} → **{new_rating1}** ({new_rating1 - rating1:+})\n"
                            f"{player2_name}: {rating2} → **{new_rating2}** ({new_rating2 - rating2:+})"
                        ),
                        color=discord.Color.orange(),
                    )

                    # Отправляем уведомление игрокам
                    if user1_id:
                        user1 = await global_bot.fetch_user(user1_id)
                        await user1.send(embed=embed_dm)
                        print(f"Уведомление отправлено {player1_name} ({user1_id})")

                    if user2_id:
                        user2 = await global_bot.fetch_user(user2_id)
                        await user2.send(embed=embed_dm)
                        print(f"Уведомление отправлено {player2_name} ({user2_id})")

                    # Отправляем ОДНО напоминание о результате (в общий чат матча)
                    # Находим канал, где был создан матч
                    try:
                        # Получаем информацию о канале из первого игрока в очереди
                        for queue in queues.values():
                            for player in queue:
                                if player["nickname"] in [
                                    player1_name,
                                    player2_name,
                                ]:
                                    channel_id = player["channel_id"]
                                    channel = global_bot.get_channel(channel_id)
                                    if channel:
                                        await channel.send(RESULT_REMINDER)
                                        print(
                                            f"Напоминание отправлено в канал #{channel.name}"
                                        )
                                        break
                            else:
                                continue
                            break
                    except Exception as e:
                        print(f"Ошибка при отправке напоминания в канал: {e}")

                except Exception as e:
                    print(f"Ошибка при отправке уведомления игрокам: {e}")

                # Отправка в канал результатов
                try:
                    embed_channel = discord.Embed(
                        title="⏱ Матч завершен (время вышло)",
                        description=(
                            f"**Match ID:** {match_id}\n"
                            f"**Режим:** {MODE_NAMES.get(mode, 'Unknown')}\n"
                            f"**Игроки:** {player1_name} vs {player2_name}\n"
                            f"**Результат:** Ничья 0:0\n\n"
                            f"**Причина:** Превышено максимальное время матча (1 час)\n\n"
                            f"**Изменения ELO:**\n"
                            f"{player1_name}: {rating1} → **{new_rating1}** ({new_rating1 - rating1:+})\n"
                            f"{player2_name}: {rating2} → **{new_rating2}** ({new_rating2 - rating2:+})"
                        ),
                        color=discord.Color.gold(),  # Желтый цвет
                    )
                    embed_channel.set_footer(
                        text="Матч завершен автоматически по истечении времени"
                    )

                    # Ищем канал elobot-results
                    results_channel_found = None
                    for guild in global_bot.guilds:
                        for channel in guild.text_channels:
                            if channel.name == "elobot-results":
                                results_channel_found = channel
                                print(
                                    f"Найден канал результатов: {channel.name} ({channel.id}) на сервере {guild.name}"
                                )
                                break
                        if results_channel_found:
                            break

                    if results_channel_found:
                        await results_channel_found.send(embed=embed_channel)
                        print(
                            f"Сообщение о матче {match_id} отправлено в канал результатов"
                        )
                    else:
                        print("⚠ Канал elobot-results не найден ни на одном сервере")
                except Exception as e:
                    print(f"Ошибка при отправке в канал: {e}")

        except Exception as e:
            print(f"Критическая ошибка в check_expired_matches: {e}")
            with open("bot_errors.log", "a") as f:
                f.write(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR in check_expired_matches: {e}\n"
                )


def setup(bot):
    global global_bot
    global_bot = bot

    @bot.event
    async def on_message(message):
        # Важно: сначала обработать команды бота
        await bot.process_commands(message)

        # Обрабатываем только личные сообщения не от бота
        if not isinstance(message.channel, discord.DMChannel) or message.author.bot:
            return

    @bot.command()
    async def play(ctx):
        # Проверка канала
        if ctx.channel.name != "elobot-queue":
            return

        player_data = db_manager.execute(
            "players",
            "SELECT playername, in_queue FROM players WHERE discordid = ?",
            (str(ctx.author.id),),
        ).fetchone()

        if not player_data:
            await ctx.send("❌ Требуется верификация для поиска игры")
            return

        # Получаем информацию об игроке
        player_data = db_manager.execute(
            "players",
            "SELECT playername, in_queue FROM players WHERE discordid = ?",
            (str(ctx.author.id),),
        ).fetchone()

        if not player_data:
            await ctx.send("❌ Вы не зарегистрированы в системе")
            return

        nickname, in_queue = player_data

        # +++ ПРОВЕРКА АКТИВНЫХ МАТЧЕЙ +++
        # Проверяем только обычные матчи (matchtype=1)
        active_normal_match = db_manager.execute(
            "matches",
            """
            SELECT matchid 
            FROM matches 
            WHERE (player1 = ? OR player2 = ?) 
            AND isover = 0
            AND matchtype = 1
            """,
            (nickname, nickname),
        ).fetchone()

        if active_normal_match:
            await ctx.send(
                f"❌ У вас есть активный обычный матч (ID: {active_normal_match[0]}). "
                "Завершите его или сдайтесь командой .giveup перед поиском новой игры."
            )
            return
        # --- КОНЕЦ ПРОВЕРКИ АКТИВНЫХ МАТЧЕЙ ---

        if in_queue == 1:
            await ctx.send("❌ Вы уже в очереди")
            return

        # Выбор режима
        view = ModeSelectView(ctx.author.id)
        msg = await ctx.send("Выберите режим игры:", view=view)

        await view.wait()

        if view.selected_mode is None:  # Таймаут или отмена
            await msg.edit(content="⌛ Время выбора истекло", view=None)
            return

        # Добавление в очередь
        rating = get_player_rating(nickname, view.selected_mode)

        queues[view.selected_mode].append(
            {
                "discord_id": ctx.author.id,
                "nickname": nickname,
                "rating": rating,
                "channel_id": ctx.channel.id,
                "join_time": datetime.now(),
            }
        )
        save_queues_to_db()

        db_manager.execute(
            "players",
            "UPDATE players SET in_queue = 1 WHERE discordid = ?",
            (str(ctx.author.id),),
        )

        await msg.edit(
            content=f"🔍 Поиск игры в режиме {MODE_NAMES[view.selected_mode]}...",
            view=None,
        )

    @bot.command()
    async def leave(ctx):
        if ctx.channel.name != "elobot-queue":
            return

        player_data = db_manager.execute(
            "players",
            "SELECT playername, in_queue FROM players WHERE discordid = ?",
            (str(ctx.author.id),),
        ).fetchone()

        if not player_data or player_data[1] == 0:
            await ctx.send("❌ Вы не в очереди")
            return

        # Удаление из всех очередей
        for mode, queue in queues.items():
            queues[mode] = [p for p in queue if p["discord_id"] != ctx.author.id]
        save_queues_to_db()

        db_manager.execute(
            "players",
            "UPDATE players SET in_queue = 0 WHERE discordid = ?",
            (str(ctx.author.id),),
        )
        await ctx.send("✅ Вы вышли из очереди")

    @bot.command()
    async def queue(ctx):
        # Проверка канала
        if ctx.channel.name != "elobot-queue":
            return

        # Создаем Embed
        embed = discord.Embed(
            title="📊 Статистика очередей", color=discord.Color.blue()
        )

        # Собираем статистику по режимам
        display_order = [
            (MODES["mots"], "MotS Solo", "🔫"),
            (MODES["12min"], "12 Minute", "⏱️"),
            (MODES["station5f"], "Station 5 Flags", "🚩"),
            (MODES["any"], "Any Mode", "🎲"),
        ]

        # Получаем количество игроков в каждой очереди
        for mode_id, mode_name, emoji in display_order:
            count = len(queues[mode_id])
            embed.add_field(
                name=f"{emoji} {mode_name}",
                value=f"`{count}` игроков в очереди",
                inline=True,
            )

        # Получаем общее количество игроков в активных матчах
        total_in_queue = (
            db_manager.execute(
                "players", "SELECT COUNT(*) FROM players WHERE in_queue = 1"
            ).fetchone()[0]
            or 0
        )

        # Получаем количество игроков в активных матчах
        total_in_matches = (
            db_manager.execute(
                "matches",
                """
            SELECT COUNT(DISTINCT player) 
            FROM (
                SELECT player1 AS player FROM matches WHERE isover = 0
                UNION ALL
                SELECT player2 AS player FROM matches WHERE isover = 0
            )
            """,
            ).fetchone()[0]
            or 0
        )

        # Общее количество игроков "в игре"
        total_in_game = total_in_queue + total_in_matches

        # Добавляем общую информацию
        embed.description = (
            f"**Всего игроков в игре:** `{total_in_game}`\n"
            f"• В очередях: `{total_in_queue}`\n"
            f"• В активных матчах: `{total_in_matches}`"
        )

        # Добавляем время последнего обновления
        embed.set_footer(
            text=f"Последнее обновление: {datetime.now().strftime('%H:%M:%S')}"
        )

        await ctx.send(embed=embed)

    @bot.command()
    async def result(ctx, match_id: int, scores: str):
        """Отправка результата матча с приложенным скриншотом"""
        # Проверяем, что команда вызвана в ЛС
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(
                "❌ Эта команда доступна только в личных сообщениях с ботом."
            )
            return

        if match_id in pending_reports:
            await ctx.send(
                "❌ Нельзя отправить результат: по этому матчу есть активный репорт."
            )
            return

        # Проверяем формат счета
        if not re.match(r"^\d+-\d+$", scores):
            await ctx.send(
                "❌ Неверный формат счета. Используйте: `.result <ID матча> <счет-игрока1>-<счет-игрока2>`"
            )
            return

        # Проверяем равенство счета
        score1, score2 = map(int, scores.split("-"))
        if score1 == score2:
            await ctx.send(
                "❌ Счет не может быть равным! Матч должен иметь победителя."
            )
            return

        # Проверяем наличие скриншота
        if not ctx.message.attachments:
            await ctx.send("❌ Пожалуйста, прикрепите скриншот с результатом матча.")
            return

        screenshot = ctx.message.attachments[0].url

        # Проверяем существование матча
        match_data = db_manager.execute(
            "matches",
            "SELECT player1, player2, mode, matchtype FROM matches WHERE matchid = ?",
            (match_id,),
        ).fetchone()

        if not match_data:
            await ctx.send("❌ Матч с указанным ID не найден.")
            return

        player1, player2, mode, matchtype = match_data

        # Проверяем, что игрок участвует в матче
        player_data = db_manager.execute(
            "players",
            "SELECT playername FROM players WHERE discordid = ?",
            (str(ctx.author.id),),
        ).fetchone()

        if not player_data:
            await ctx.send("❌ Вы не зарегистрированы в системе.")
            return

        submitter_name = player_data[0]

        if submitter_name not in [player1, player2]:
            await ctx.send("❌ Вы не участвуете в этом матче.")
            return

        # Определяем оппонента
        opponent_name = player2 if submitter_name == player1 else player1

        # Получаем discord_id оппонента
        opponent_data = db_manager.execute(
            "players",
            "SELECT discordid FROM players WHERE playername = ?",
            (opponent_name,),
        ).fetchone()

        if not opponent_data:
            await ctx.send("❌ Не удалось найти оппонента в системе")
            return

        opponent_id = int(opponent_data[0])

        # Сохраняем результат для подтверждения оппонентом
        pending_player_confirmations[match_id] = {
            "match_id": match_id,
            "player1": player1,
            "player2": player2,
            "scores": scores,
            "screenshot": screenshot,
            "submitter_id": ctx.author.id,
            "submitter_name": submitter_name,
            "opponent_id": opponent_id,
            "opponent_name": opponent_name,
            "mode": mode,
            "timestamp": datetime.now(),
        }

        # Отправляем запрос подтверждения оппоненту
        try:
            opponent_user = await global_bot.fetch_user(opponent_id)

            embed = discord.Embed(
                title="🔔 Требуется подтверждение результата",
                description=(
                    f"Ваш противник отправил результат матча #{match_id}\n"
                    f"**Счет:** {scores}\n\n"
                    f"Пожалуйста, подтвердите результат если он верен, "
                    f"или оспорьте если есть расхождения."
                ),
                color=discord.Color.orange(),
            )

            if screenshot:
                embed.set_image(url=screenshot)

            # Обновляем информацию о времени (1 час)
            embed.set_footer(text="У вас есть 1 час на подтверждение")

            view = PlayerConfirmationView(match_id, ctx.author.id, opponent_id)
            msg = await opponent_user.send(embed=embed, view=view)
            view.message = msg

            await ctx.send("✅ Результат отправлен вашему оппоненту на подтверждение!")

        except Exception as e:
            print(f"Ошибка отправки подтверждения: {e}")
            await ctx.send(
                "❌ Не удалось отправить запрос подтверждения оппоненту. Обратитесь к администратору."
            )

        if matchtype == 2:
            # Получаем название турнира из БД
            tournament_data = db_manager.fetchone(
                "matches",
                "SELECT tournament_id FROM matches WHERE matchid = ?",
                (match_id,),
            )

            if tournament_data:
                tournament_name = tournament_data[0]
                results_channel = discord.utils.get(
                    bot.get_all_channels(), name=f"{tournament_name}-results"
                )

                if results_channel:
                    # Создаем embed для турнирного канала
                    embed = discord.Embed(
                        title=f"🏆 Турнирный матч завершен | ID: {match_id}",
                        description=(
                            f"**Игроки:** {player1} vs {player2}\n"
                            f"**Счет:** {scores}\n"
                            f"**Победитель:** {player1 if int(scores.split('-')[0]) > int(scores.split('-')[1]) else player2}"
                        ),
                        color=discord.Color.green(),
                    )

                    if ctx.message.attachments:
                        embed.set_image(url=ctx.message.attachments[0].url)

                    await results_channel.send(embed=embed)

    @bot.command()
    async def giveup(ctx):
        # Проверяем, что команда вызвана в нужном канале или в ЛС боту
        if (
            not isinstance(ctx.channel, discord.DMChannel)
            and ctx.channel.name != "elobot-queue"
        ):
            return

        # ПРОВЕРКА ВЕРИФИКАЦИИ ЧЕРЕЗ БАЗУ ДАННЫХ
        player_data = db_manager.execute(
            "players",
            "SELECT playername FROM players WHERE discordid = ?",
            (str(ctx.author.id),),
        ).fetchone()
        if not player_data:
            await ctx.send("❌ Требуется верификация для использования этой команды")
            return
        # Находим активный матч игрока
        player_data = db_manager.execute(
            "players",
            "SELECT playername FROM players WHERE discordid = ?",
            (str(ctx.author.id),),
        ).fetchone()

        if not player_data:
            await ctx.send("❌ Вы не зарегистрированы в системе")
            return

        nickname = player_data[0]

        match_data = db_manager.execute(
            "matches",
            """
            SELECT matchid, mode, player1, player2, matchtype
            FROM matches 
            WHERE (player1 = ? OR player2 = ?) 
            AND isover = 0
            """,
            (nickname, nickname),
        ).fetchone()

        if not match_data:
            await ctx.send("❌ У вас нет активных матчей")
            return

        match_id, mode, player1, player2, matchtype = match_data

        # Определяем победителя и проигравшего
        if nickname == player1:
            winner = player2
            loser = player1
            player1_score = 0
            player2_score = 1
        else:
            winner = player1
            loser = player2
            player1_score = 1
            player2_score = 0

        # Обновляем запись матча
        db_manager.execute(
            "matches",
            """
            UPDATE matches 
            SET player1score = ?, player2score = ?, isover = 1, isverified = 1
            WHERE matchid = ?
            """,
            (player1_score, player2_score, match_id),
        )

        # Обновляем статистику игроков
        db_manager.execute(
            "players",
            "UPDATE players SET wins = wins + 1 WHERE playername = ?",
            (winner,),
        )
        db_manager.execute(
            "players",
            "UPDATE players SET losses = losses + 1 WHERE playername = ?",
            (loser,),
        )

        # Обновляем ELO
        winner_rating = get_player_rating(winner, mode)
        loser_rating = get_player_rating(loser, mode)

        # Всегда передаём 1 (победа победителя)
        new_winner_rating, new_loser_rating = calculate_elo(
            winner_rating, loser_rating, 1
        )

        update_player_rating(winner, new_winner_rating, mode)
        update_player_rating(loser, new_loser_rating, mode)
        # КОНЕЦ ИСПРАВЛЕНИЯ

        # Отправляем уведомление
        mode_name = MODE_NAMES.get(mode, "Unknown")
        embed = discord.Embed(
            title="🏳️ Матч завершен (сдача)",
            description=(
                f"**Match ID:** {match_id}\n"
                f"**Режим:** {mode_name}\n"
                f"**Победитель:** {winner}\n"
                f"**Проигравший:** {loser}\n\n"
                f"**Изменения ELO:**\n"
                f"{winner}: {winner_rating} → **{new_winner_rating}** (+{new_winner_rating - winner_rating})\n"
                f"{loser}: {loser_rating} → **{new_loser_rating}** ({new_loser_rating - loser_rating})"
            ),
            color=discord.Color.red(),
        )

        embed_channel = discord.Embed(  ## embed для отправки в канал результатов
            title="🏳️ Матч завершен (сдача)",
            description=(
                f"**Match ID:** {match_id}\n"
                f"**Режим:** {mode_name}\n"
                f"**Победитель:** {winner}\n"
                f"**Проигравший:** {loser}\n\n"
                f"**Изменения ELO:**\n"
                f"{winner}: {winner_rating} → **{new_winner_rating}** (+{new_winner_rating - winner_rating})\n"
                f"{loser}: {loser_rating} → **{new_loser_rating}** ({new_loser_rating - loser_rating})"
            ),
            color=discord.Color.red(),
        )

        # +++ ДОБАВЛЯЕМ ОТПРАВКУ В КАНАЛ РЕЗУЛЬТАТОВ +++
        # Ищем канал elobot-results
        results_channel_found = None
        for guild in global_bot.guilds:
            results_channel = discord.utils.get(
                guild.text_channels, name="elobot-results"
            )
            if results_channel:
                results_channel_found = results_channel
                break

        if results_channel_found:
            try:
                await results_channel_found.send(embed=embed_channel)
            except Exception as e:
                print(f"Ошибка при отправке в канал результатов: {e}")
                # Пытаемся отправить в канал очереди как запасной вариант
                try:
                    if isinstance(ctx.channel, discord.TextChannel):
                        await ctx.send(
                            f"⚠ Не удалось отправить в канал результатов: {e}"
                        )
                except:
                    pass
        else:
            print("Канал elobot-results не найден ни на одном сервере")
            try:
                if isinstance(ctx.channel, discord.TextChannel):
                    await ctx.send("⚠ Канал elobot-results не найден")
            except:
                pass

        if isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("✅ Вы сдались. Матч завершен.")
        else:
            await ctx.send(
                "✅ Вы сдались. Матч завершен. Результаты отправлены обоим игрокам."
            )

        try:
            # Отправляем в ЛС обоим игрокам
            winner_user = await global_bot.fetch_user(
                get_discord_id_by_nickname(winner)
            )
            loser_user = await global_bot.fetch_user(get_discord_id_by_nickname(loser))

            await winner_user.send(embed=embed)
            await loser_user.send(embed=embed)
        except Exception as e:
            print(f"Ошибка при отправке уведомления о сдаче: {e}")

    @bot.command()
    async def report(ctx, match_id: int, *, reason: str):
        """Отправка репорта на матч"""
        # Проверяем, что команда вызвана в ЛС или канале очереди
        if (
            not isinstance(ctx.channel, discord.DMChannel)
            and ctx.channel.name != "elobot-queue"
        ):
            return

        # Проверяем существование матча
        match_data = db_manager.execute(
            "matches",
            "SELECT player1, player2 FROM matches WHERE matchid = ?",
            (match_id,),
        ).fetchone()

        if not match_data:
            await ctx.send("❌ Матч с указанным ID не найден.")
            return

        player1, player2 = match_data

        # Проверяем, что игрок участвует в матче
        player_data = db_manager.execute(
            "players",
            "SELECT playername FROM players WHERE discordid = ?",
            (str(ctx.author.id),),
        ).fetchone()

        if not player_data:
            await ctx.send("❌ Вы не зарегистрированы в системе")
            return

        reporter_name = player_data[0]

        if reporter_name not in [player1, player2]:
            await ctx.send("❌ Вы не участвуете в этом матче.")
            return

        # Определяем нарушителя (противник репортера)
        violator_name = player2 if reporter_name == player1 else player1

        # Помечаем матч как завершенный (но не проверенный)
        db_manager.execute(
            "matches", "UPDATE matches SET isover = 1 WHERE matchid = ?", (match_id,)
        )

        # Сохраняем скриншот если есть
        screenshot_url = None
        if ctx.message.attachments:
            screenshot_url = ctx.message.attachments[0].url

        # Сохраняем репорт
        pending_reports[match_id] = {
            "reporter_id": ctx.author.id,
            "reporter_name": reporter_name,
            "violator_name": violator_name,
            "reason": reason,
            "screenshot": screenshot_url,
        }

        # Отправляем модератору
        try:
            moderator = await global_bot.fetch_user(MODERATOR_ID)

            embed = discord.Embed(
                title="⚠️ Новый репорт",
                description=(
                    f"**Match ID:** {match_id}\n"
                    f"**Репортер:** {reporter_name}\n"
                    f"**Нарушитель:** {violator_name}\n"
                    f"**Причина:** {reason}"
                ),
                color=discord.Color.orange(),
            )

            if screenshot_url:
                embed.set_image(url=screenshot_url)

            view = ReportView(match_id, reporter_name, violator_name)
            await moderator.send(embed=embed, view=view)

            await ctx.send(
                "✅ Репорт отправлен на рассмотрение. Матч временно заморожен."
            )
        except Exception as e:
            print(f"Ошибка при отправке репорта: {e}")
            await ctx.send(
                "❌ Не удалось отправить репорт. Обратитесь к администратору."
            )


class ConfirmMatchView(View):
    def __init__(self, match_id, bot, result_message_id):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.bot = bot
        self.result_message_id = result_message_id

    @discord.ui.button(label="Подтвердить", style=discord.ButtonStyle.green)
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Получаем данные о результате из pending_results
        result_data = pending_results.get(self.result_message_id)
        if not result_data:
            await interaction.response.send_message(
                "Данные о результате устарели или не найдены", ephemeral=True
            )
            return

        # Извлекаем счет из данных, отправленных игроком
        try:
            score1, score2 = map(int, result_data["scores"].split("-"))
        except ValueError:
            await interaction.response.send_message(
                "Неверный формат счета в данных результата", ephemeral=True
            )
            return

        # Проверяем равенство счета
        if score1 == score2:
            await interaction.response.send_message(
                "❌ Счет не может быть равным! Матч должен иметь победителя",
                ephemeral=True,
            )
            return

        # Получаем информацию о матче из БД
        match = db_manager.execute(
            "matches",
            "SELECT mode, player1, player2, map FROM matches WHERE matchid = ?",
            (self.match_id,),
        ).fetchone()

        if not match:
            await interaction.response.send_message("Матч не найден", ephemeral=True)
            return

        mode, player1, player2, map_name = match
        mode_name = MODE_NAMES.get(mode, "Unknown")

        # Определяем предполагаемого победителя по счету
        if score1 > score2:
            presumed_winner = player1
        else:
            presumed_winner = player2

        # Проверяем, что результат отправил победитель
        submitter_data = db_manager.execute(
            "players",
            "SELECT playername FROM players WHERE discordid = ?",
            (str(result_data["submitted_by"]),),
        ).fetchone()

        if not submitter_data:
            await interaction.response.send_message(
                "❌ Не удалось идентифицировать отправителя результата", ephemeral=True
            )
            return

        submitter_name = submitter_data[0]

        if submitter_name != presumed_winner:
            await interaction.response.send_message(
                f"❌ Результат должен отправлять победитель матча ({presumed_winner})!",
                ephemeral=True,
            )
            return

        # Определяем результат на основе счета
        if score1 > score2:
            result = 1  # Победа player1
        elif score1 < score2:
            result = 0  # Победа player2
        else:
            result = 0.5  # Ничья

        # Получаем текущие рейтинги
        old_rating1 = get_player_rating(player1, mode)
        old_rating2 = get_player_rating(player2, mode)

        # Рассчитываем новые рейтинги
        new_rating1, new_rating2 = calculate_elo(old_rating1, old_rating2, result)

        # Обновляем рейтинги
        update_player_rating(player1, new_rating1, mode)
        update_player_rating(player2, new_rating2, mode)

        # Обновляем статистику
        if result == 1:
            db_manager.execute(
                "players",
                "UPDATE players SET wins = wins + 1 WHERE playername = ?",
                (player1,),
            )
            db_manager.execute(
                "players",
                "UPDATE players SET losses = losses + 1 WHERE playername = ?",
                (player2,),
            )
        elif result == 0:
            db_manager.execute(
                "players",
                "UPDATE players SET wins = wins + 1 WHERE playername = ?",
                (player2,),
            )
            db_manager.execute(
                "players",
                "UPDATE players SET losses = losses + 1 WHERE playername = ?",
                (player1,),
            )
        else:  # Ничья
            db_manager.execute(
                "players",
                "UPDATE players SET ties = ties + 1 WHERE playername IN (?, ?)",
                (player1, player2),
            )

        # Обновляем счетчики для конкретного режима
        if mode == MODES["station5f"]:
            if result == 1:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_station5f = wins_station5f + 1 WHERE playername = ?",
                    (player1,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_station5f = losses_station5f + 1 WHERE playername = ?",
                    (player2,),
                )
            elif result == 0:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_station5f = wins_station5f + 1 WHERE playername = ?",
                    (player2,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_station5f = losses_station5f + 1 WHERE playername = ?",
                    (player1,),
                )
            else:
                db_manager.execute(
                    "players",
                    "UPDATE players SET ties_station5f = ties_station5f + 1 WHERE playername = ?",
                    (player1,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET ties_station5f = ties_station5f + 1 WHERE playername = ?",
                    (player2,),
                )
        elif mode == MODES["mots"]:
            if result == 1:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_mots = wins_mots + 1 WHERE playername = ?",
                    (player1,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_mots = losses_mots + 1 WHERE playername = ?",
                    (player2,),
                )
            elif result == 0:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_mots = wins_mots + 1 WHERE playername = ?",
                    (player2,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_mots = losses_mots + 1 WHERE playername = ?",
                    (player1,),
                )
            else:
                db_manager.execute(
                    "players",
                    "UPDATE players SET ties_mots = ties_mots + 1 WHERE playername = ?",
                    (player1,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET ties_mots = ties_mots + 1 WHERE playername = ?",
                    (player2,),
                )
        elif mode == MODES["12min"]:
            if result == 1:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_12min = wins_12min + 1 WHERE playername = ?",
                    (player1,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_12min = losses_12min + 1 WHERE playername = ?",
                    (player2,),
                )
            elif result == 0:
                db_manager.execute(
                    "players",
                    "UPDATE players SET wins_12min = wins_12min + 1 WHERE playername = ?",
                    (player2,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET losses_12min = losses_12min + 1 WHERE playername = ?",
                    (player1,),
                )
            else:
                db_manager.execute(
                    "players",
                    "UPDATE players SET ties_12min = ties_12min + 1 WHERE playername = ?",
                    (player1,),
                )
                db_manager.execute(
                    "players",
                    "UPDATE players SET ties_12min = ties_12min + 1 WHERE playername = ?",
                    (player2,),
                )

        # Обновляем запись матча с полученным счетом
        db_manager.execute(
            "matches",
            "UPDATE matches SET player1score = ?, player2score = ?, isover = 1, isverified = 1 WHERE matchid = ?",
            (score1, score2, self.match_id),
        )

        # Удаляем результат из ожидающих
        if self.result_message_id in pending_results:
            del pending_results[self.result_message_id]

        # Рассчитываем изменения ELO
        elo_change1 = new_rating1 - old_rating1
        elo_change2 = new_rating2 - old_rating2

        # Отправляем отчёт в канал результатов
        for guild in self.bot.guilds:
            results_channel = discord.utils.get(
                guild.text_channels, name="elobot-results"
            )
            if results_channel:
                result_embed = discord.Embed(
                    title=f"✅ Матч завершен | ID: {self.match_id}",
                    description=(
                        f"**Режим:** {mode_name}\n"
                        f"**Карта:** {map_name if map_name else 'не выбрана'}\n"
                        f"**Игроки:** {player1} vs {player2}\n"
                        f"**Счёт:** {score1} - {score2}\n\n"
                        f"**Изменения ELO ({mode_name}):**\n"
                        f"{player1}: {old_rating1} → **{new_rating1}** ({'+' if elo_change1 >= 0 else ''}{elo_change1})\n"
                        f"{player2}: {old_rating2} → **{new_rating2}** ({'+' if elo_change2 >= 0 else ''}{elo_change2})"
                    ),
                    color=discord.Color.green(),
                )
                await results_channel.send(embed=result_embed)
                break

        # Уведомляем игроков
        try:
            player1_id = get_discord_id_by_nickname(player1)
            player2_id = get_discord_id_by_nickname(player2)

            if player1_id:
                user1 = await global_bot.fetch_user(player1_id)
                await user1.send(
                    f"✅ Результат вашего матча #{self.match_id} подтвержден!"
                )

            if player2_id:
                user2 = await global_bot.fetch_user(player2_id)
                await user2.send(
                    f"✅ Результат вашего матча #{self.match_id} подтвержден!"
                )
        except Exception as e:
            print(f"Ошибка уведомления игроков: {e}")

        await interaction.response.send_message("Матч подтвержден!", ephemeral=True)
        await interaction.message.edit(view=None)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red)
    async def reject_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Получаем данные о результате
        result_data = pending_results.get(self.result_message_id)
        if not result_data:
            await interaction.response.send_message(
                "Данные о результате устарели или не найдены", ephemeral=True
            )
            return

        # Уведомляем отправителя
        try:
            user = await global_bot.fetch_user(result_data["submitted_by"])
            await user.send(
                f"❌ Ваш результат для матча {self.match_id} был отклонен модератором."
            )
        except Exception as e:
            print(f"Ошибка уведомления об отклонении: {e}")

        # Удаляем результат из ожидающих
        if self.result_message_id in pending_results:
            del pending_results[self.result_message_id]

        # Помечаем матч как отклоненный
        db_manager.execute(
            "matches",
            "UPDATE matches SET isverified = 2 WHERE matchid = ?",
            (self.match_id,),
        )

        # Логирование отклонения
        guild = interaction.guild
        if guild:
            logs_channel = discord.utils.get(guild.text_channels, name="elobot-logs")
            if logs_channel:
                await logs_channel.send(
                    f"❌ Результат матча {self.match_id} отклонен модератором"
                )

        await interaction.response.send_message("Результат отклонен", ephemeral=True)
        await interaction.message.edit(view=None)
