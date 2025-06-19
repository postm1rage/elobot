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
    MODE_NAMES,
    LEADERBOARD_MODES,
    MODES,
)
from verification import (
    setup_verified_role,
    setup as setup_verification,
    VerifyView,
)  # Добавлен VerifyView
from queueing import setup as setup_queueing, ConfirmMatchView, find_match
import re
from discord.ui import View, Button, Select
import discord


load_dotenv()
token = os.getenv("DISCORD_TOKEN")

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")


@bot.event
async def on_ready():
    print(f"Бот {bot.user.name} запущен!")

    # Проверяем и создаём необходимые роли/каналы
    for guild in bot.guilds:
        # Создаём роль verified
        await setup_verified_role(guild)

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
    SELECT playerid, playername, discordid, currentelo, 
           elo_station5f, elo_mots, elo_12min,
           wins, losses, ties, currentmatches
    FROM players
    WHERE playername = ?
    """,
        (nickname,),
    )

    player = c.fetchone()

    if player:
        player_data = {
            "id": player[0],
            "name": player[1],
            "discord_id": player[2],
            "elo": player[3],
            "elo_station5f": player[4],
            "elo_mots": player[5],
            "elo_12min": player[6],
            "wins": player[7],
            "losses": player[8],
            "ties": player[9],
            "matches": player[10],
        }

        embed = discord.Embed(
            title=f"Информация об игроке {player_data['name']}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="ID", value=player_data["id"], inline=True)
        embed.add_field(name="Discord ID", value=player_data["discord_id"], inline=True)
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

    if message.channel.name == VERIFY_CHANNEL_NAME:
        if not message.content.strip():
            await message.delete()
            return

        try:
            # Проверка 1: Наличие скриншота
            if not message.attachments:
                results_channel = discord.utils.get(
                    message.guild.text_channels, name=RESULTS_CHANNEL_NAME
                )
                if results_channel:
                    embed = discord.Embed(
                        title="❌ Верификация отклонена (автоматически)",
                        description=(
                            f"Игрок {message.author.mention}\n"
                            f"Причина: Отсутствует скриншот\n"
                            f"Никнейм: {message.content}"
                        ),
                        color=discord.Color.red(),
                    )
                    await results_channel.send(embed=embed)
                await message.delete()
                return

            # Проверка 2: Существующий Discord ID
            c = db.cursor()
            c.execute(
                "SELECT 1 FROM players WHERE discordid = ?", (str(message.author.id),)
            )
            if c.fetchone():
                results_channel = discord.utils.get(
                    message.guild.text_channels, name=RESULTS_CHANNEL_NAME
                )
                if results_channel:
                    embed = discord.Embed(
                        title="❌ Верификация отклонена (автоматически)",
                        description=(
                            f"Игрок {message.author.mention}\n"
                            f"Причина: Discord ID уже зарегистрирован\n"
                            f"Никнейм: {message.content}"
                        ),
                        color=discord.Color.red(),
                    )
                    await results_channel.send(embed=embed)
                await message.delete()
                return

            # Проверка 3: Существующее имя игрока
            c.execute(
                "SELECT 1 FROM players WHERE playername = ?", (message.content.strip(),)
            )
            if c.fetchone():
                results_channel = discord.utils.get(
                    message.guild.text_channels, name=RESULTS_CHANNEL_NAME
                )
                if results_channel:
                    embed = discord.Embed(
                        title="❌ Верификация отклонена (автоматически)",
                        description=(
                            f"Игрок {message.author.mention}\n"
                            f"Причина: Никнейм уже занят\n"
                            f"Никнейм: {message.content}"
                        ),
                        color=discord.Color.red(),
                    )
                    await results_channel.send(embed=embed)
                await message.delete()
                return

            # Если все проверки пройдены - отправляем модератору
            moderator = await bot.fetch_user(MODERATOR_ID)
            embed = discord.Embed(
                title="Новая заявка на верификацию",
                description=f"**Никнейм:** {message.content}\n**Отправитель:** {message.author.mention}",
                color=discord.Color.blue(),
            )
            embed.set_footer(text=f"ID: {message.id}")

            files = [await attachment.to_file() for attachment in message.attachments]
            view = VerifyView(message.id, message.guild.id, message.content.strip())

            await moderator.send(embed=embed, files=files, view=view)
        except Exception as e:
            print(f"Ошибка при обработке верификации: {e}")
            try:
                results_channel = discord.utils.get(
                    message.guild.text_channels, name=RESULTS_CHANNEL_NAME
                )
                if results_channel:
                    await results_channel.send(
                        f"⚠️ Ошибка при обработке верификации: {str(e)}"
                    )
            except:
                pass
        finally:
            return

    # Обработка результатов матча
    if message.channel.name == "elobot-results" and message.attachments:
        # Парсим счет
        score_match = re.search(r"(\d+)\s*-\s*(\d+)", message.content)
        if not score_match:
            return

        score1 = int(score_match.group(1))
        score2 = int(score_match.group(2))

        # Ищем активный матч игрока
        c = db.cursor()
        c.execute(
            "SELECT playername FROM players WHERE discordid = ?",
            (str(message.author.id),),
        )
        player_data = c.fetchone()
        if not player_data:
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

        view = ConfirmMatchView(match_id, bot)

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

    await bot.process_commands(message)


# Закрытие базы данных при завершении
@bot.event
async def on_disconnect():
    matches_db.close()
    db.close()
    print("Базы данных закрыты")


# Настройка модулей
setup_verification(bot)
setup_queueing(bot)

bot.run(token, log_handler=handler, log_level=logging.DEBUG)
