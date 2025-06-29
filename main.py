import logging
from dotenv import load_dotenv
import os
from datetime import datetime
from config import (
    bot,
    db,
    matches_db,
    MODERATOR_ID,
    MODE_NAMES,
    VERIFY_CHANNEL_NAME,
    RESULTS_CHANNEL_NAME,
    MODERATOR_ID,
    LEADERBOARD_MODES,
    MODES,
)
from verification import (
    setup_verified_role,
    setup as setup_verification,
    VerifyView,
)  # Добавлен VerifyView
from queueing import setup as setup_queueing, ConfirmMatchView, find_match
from queueing import check_expired_matches
import re
from discord.ui import View, Button, Select
import discord


load_dotenv()
token = os.getenv("DISCORD_TOKEN")

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")


@bot.event
async def on_ready():
    print(f"Бот {bot.user.name} запущен!")

    # Создаем фоновую задачу
    bot.loop.create_task(check_expired_matches(bot))

    # Проверяем и создаём необходимые роли/каналы
    for guild in bot.guilds:
        queue_channel = discord.utils.get(guild.text_channels, name="elobot-queue")
        results_channel = discord.utils.get(guild.text_channels, name="elobot-results")

        if not queue_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала 'elobot-queue'")
        if not results_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала 'elobot-results'")

        verify_channel = discord.utils.get(guild.text_channels, name="elobot-verify")
        logs_channel = discord.utils.get(guild.text_channels, name="elobot-logs")

        if not verify_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала 'elobot-verify'")
        if not logs_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала 'elobot-logs'")


@bot.event
async def setup_hook():
    """Асинхронный хук для запуска фоновых задач"""
    bot.loop.create_task(find_match())


@bot.command()
async def playerinfo(ctx, nickname: str):
    """Показывает информацию об игроке"""
    c = db.cursor()
    c.execute(
        """
    SELECT playerid, playername, currentelo, 
           elo_station5f, elo_mots, elo_12min,
           wins, losses, ties
    FROM players
    WHERE playername = ?
    """,
        (nickname,),
    )

    player = c.fetchone()

    if player:
        # Рассчитываем общее количество матчей
        total_matches = player[6] + player[7] + player[8]  # wins + losses + ties

        player_data = {
            "id": player[0],
            "name": player[1],
            "elo": player[2],
            "elo_station5f": player[3],
            "elo_mots": player[4],
            "elo_12min": player[5],
            "wins": player[6],
            "losses": player[7],
            "ties": player[8],
            "matches": total_matches,  # Используем вычисленное значение
        }

        embed = discord.Embed(
            title=f"Информация об игроке {player_data['name']}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="ID", value=player_data["id"], inline=True)
        embed.add_field(name="Общий ELO", value=player_data["elo"], inline=True)
        embed.add_field(
            name="ELO Station", value=player_data["elo_station5f"], inline=True
        )
        embed.add_field(name="ELO MotS", value=player_data["elo_mots"], inline=True)
        embed.add_field(name="ELO 12min", value=player_data["elo_12min"], inline=True)
        embed.add_field(name="Победы", value=player_data["wins"], inline=True)
        embed.add_field(name="Поражения", value=player_data["losses"], inline=True)
        embed.add_field(name="Ничьи", value=player_data["ties"], inline=True)
        embed.add_field(name="Всего матчей", value=player_data["matches"], inline=True)

        await ctx.send(embed=embed)
    else:
        await ctx.send(f"Игрок с ником '{nickname}' не найден")


@bot.command()
async def leaderboard(ctx):
    """Показывает таблицу лидеров с статистикой по режимам"""

    class LeaderboardView(View):
        def __init__(self):
            super().__init__(timeout=30)
            self.selected_mode = None

            options = [
                discord.SelectOption(
                    label="Overall", value="overall", description="Общий рейтинг"
                ),
                discord.SelectOption(label="Station 5 Flags", value="station5flags"),
                discord.SelectOption(label="MotS Solo", value="mots"),
                discord.SelectOption(label="12 Minute", value="12min"),
            ]

            select = Select(placeholder="Выберите режим", options=options)
            select.callback = self.select_callback
            self.add_item(select)

        async def select_callback(self, interaction: discord.Interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message(
                    "Это не ваша команда!", ephemeral=True
                )
                return

            self.selected_mode = interaction.data["values"][0]
            await interaction.response.defer()
            self.stop()

    view = LeaderboardView()
    msg = await ctx.send("Выберите режим для таблицы лидеров:", view=view)

    if await view.wait() or not view.selected_mode:
        await msg.edit(content="Время выбора истекло", view=None)
        return

    # Получаем данные из БД
    c = db.cursor()
    elo_col, wins_col, losses_col, ties_col = LEADERBOARD_MODES[view.selected_mode]

    c.execute(
        f"""
    SELECT playername, {elo_col}, {wins_col}, {losses_col}, {ties_col}
    FROM players 
    ORDER BY {elo_col} DESC 
    LIMIT 10
    """
    )

    leaders = c.fetchall()

    # Формируем embed
    mode_names = {
        "overall": "Общий рейтинг",
        "station5flags": "Station 5 Flags",
        "mots": "MotS Solo",
        "12min": "12 Minute",
    }

    embed = discord.Embed(
        title=f"🏆 Топ-10 игроков: {mode_names[view.selected_mode]}",
        color=discord.Color.gold(),
    )

    for i, (name, elo, wins, losses, ties) in enumerate(leaders, 1):
        total = wins + losses + ties
        winrate = (wins / total * 100) if total > 0 else 0

        embed.add_field(
            name=f"{i}. {name}",
            value=(
                f"ELO: {elo}\n"
                f"Победы: {wins} | Поражения: {losses} | Ничьи: {ties}\n"
                f"Винрейт: {winrate:.1f}%"
            ),
            inline=False,
        )

    embed.set_footer(text=f"Обновлено: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    await msg.edit(content=None, embed=embed, view=None)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Добавим логгирование для диагностики
    print(f"Получено сообщение в #{message.channel.name}: {message.content[:20]}...")

    # Обработка результатов матча ТОЛЬКО в канале elobot-results
    if message.channel.name == "elobot-results" and message.attachments:
        # Парсим счет
        score_match = re.search(r"(\d+)\s*-\s*(\d+)", message.content)
        if not score_match:
            return

        score1 = int(score_match.group(1))
        score2 = int(score_match.group(2))

        # Проверяем равенство счета
        if score1 == score2:
            await message.channel.send(
                "❌ Счет не может быть равным! Матч должен иметь победителя."
            )
            return

        # Определяем победителя
        winner_score = max(score1, score2)
        loser_score = min(score1, score2)
        is_player1_winner = score1 > score2

        # Ищем активный матч игрока
        c = db.cursor()
        c.execute(
            "SELECT playername FROM players WHERE discordid = ?",
            (str(message.author.id),),
        )
        player_data = c.fetchone()
        if not player_data:
            await message.channel.send("❌ Вы не зарегистрированы в системе")
            return

        nickname = player_data[0]

        c = matches_db.cursor()
        c.execute(
            """
            SELECT matchid, player1, player2, mode 
            FROM matches 
            WHERE (player1 = ? OR player2 = ?) 
            AND isover = 0
            """,
            (nickname, nickname),
        )
        match_data = c.fetchone()

        if not match_data:
            await message.channel.send("❌ Активный матч не найден")
            return

        match_id, player1, player2, mode = match_data

        # Проверяем, что сообщение отправил победитель
        if is_player1_winner and nickname != player1:
            await message.channel.send(
                f"❌ Результат должен отправлять победитель ({player1})!"
            )
            return
        elif not is_player1_winner and nickname != player2:
            await message.channel.send(
                f"❌ Результат должен отправлять победитель ({player2})!"
            )
            return

        # Определяем порядок счета
        if nickname == player1:
            player1_score, player2_score = score1, score2
            winner, loser = player1, player2
        else:
            player1_score, player2_score = score2, score1
            winner, loser = player2, player1

        # Обновляем запись матча
        c.execute(
            """
            UPDATE matches 
            SET player1score = ?, player2score = ?
            WHERE matchid = ?
            """,
            (player1_score, player2_score, match_id),
        )
        matches_db.commit()

        # Обновляем статистику в БД
        c = db.cursor()
        if player1_score > player2_score:
            # Победа player1
            c.execute(
                "UPDATE players SET wins = wins + 1 WHERE playername = ?", (winner,)
            )
            c.execute(
                "UPDATE players SET losses = losses + 1 WHERE playername = ?", (loser,)
            )

            if mode == MODES["station5f"]:
                c.execute(
                    "UPDATE players SET wins_station5f = wins_station5f + 1 WHERE playername = ?",
                    (winner,),
                )
                c.execute(
                    "UPDATE players SET losses_station5f = losses_station5f + 1 WHERE playername = ?",
                    (loser,),
                )
            elif mode == MODES["mots"]:
                c.execute(
                    "UPDATE players SET wins_mots = wins_mots + 1 WHERE playername = ?",
                    (winner,),
                )
                c.execute(
                    "UPDATE players SET losses_mots = losses_mots + 1 WHERE playername = ?",
                    (loser,),
                )
            elif mode == MODES["12min"]:
                c.execute(
                    "UPDATE players SET wins_12min = wins_12min + 1 WHERE playername = ?",
                    (winner,),
                )
                c.execute(
                    "UPDATE players SET losses_12min = losses_12min + 1 WHERE playername = ?",
                    (loser,),
                )

        elif player1_score < player2_score:
            # Победа player2
            c.execute(
                "UPDATE players SET wins = wins + 1 WHERE playername = ?", (winner,)
            )
            c.execute(
                "UPDATE players SET losses = losses + 1 WHERE playername = ?", (loser,)
            )

            if mode == MODES["station5f"]:
                c.execute(
                    "UPDATE players SET wins_station5f = wins_station5f + 1 WHERE playername = ?",
                    (winner,),
                )
                c.execute(
                    "UPDATE players SET losses_station5f = losses_station5f + 1 WHERE playername = ?",
                    (loser,),
                )
            elif mode == MODES["mots"]:
                c.execute(
                    "UPDATE players SET wins_mots = wins_mots + 1 WHERE playername = ?",
                    (winner,),
                )
                c.execute(
                    "UPDATE players SET losses_mots = losses_mots + 1 WHERE playername = ?",
                    (loser,),
                )
            elif mode == MODES["12min"]:
                c.execute(
                    "UPDATE players SET wins_12min = wins_12min + 1 WHERE playername = ?",
                    (winner,),
                )
                c.execute(
                    "UPDATE players SET losses_12min = losses_12min + 1 WHERE playername = ?",
                    (loser,),
                )
        else:
            # Ничья
            c.execute(
                "UPDATE players SET ties = ties + 1 WHERE playername IN (?, ?)",
                (player1, player2),
            )

            if mode == MODES["station5f"]:
                c.execute(
                    "UPDATE players SET ties_station5f = ties_station5f + 1 WHERE playername IN (?, ?)",
                    (player1, player2),
                )
            elif mode == MODES["mots"]:
                c.execute(
                    "UPDATE players SET ties_mots = ties_mots + 1 WHERE playername IN (?, ?)",
                    (player1, player2),
                )
            elif mode == MODES["12min"]:
                c.execute(
                    "UPDATE players SET ties_12min = ties_12min + 1 WHERE playername IN (?, ?)",
                    (player1, player2),
                )

        db.commit()

        # Отправляем модератору на подтверждение
        moderator = await bot.fetch_user(MODERATOR_ID)
        embed = discord.Embed(
            title="⚠️ Требуется подтверждение матча",
            description=(
                f"**Match ID:** {match_id}\n"
                f"**Режим:** {MODE_NAMES.get(mode, 'Unknown')}\n"
                f"**{player1}** vs **{player2}**\n"
                f"**Счет:** {player1_score}-{player2_score}"
            ),
            color=discord.Color.orange(),
        )

        view = ConfirmMatchView(match_id, bot, message.id)

        await moderator.send(
            embed=embed,
            view=view,
            files=[await attachment.to_file() for attachment in message.attachments],
        )

        try:
            await message.delete()
            print(f"Сообщение с результатом матча удалено: {message.id}")
        except Exception as e:
            print(f"Ошибка при удалении сообщения: {e}")
        return

    # Всегда обрабатываем команды после нашей кастомной логики
    await bot.process_commands(message)


# Закрытие базы данных при завершении
@bot.event
async def on_disconnect():
    matches_db.close()
    db.close()
    print("Базы данных закрыты")


# Настройка модулей
setup_queueing(bot)
setup_verification(bot)

bot.run(token, log_handler=handler, log_level=logging.DEBUG)
