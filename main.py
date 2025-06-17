import logging
from dotenv import load_dotenv
import os
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
)
from verification import (
    setup_verified_role,
    setup as setup_verification,
    VerifyView,
)  # Добавлен VerifyView
from queueing import setup as setup_queueing, ConfirmMatchView, find_match
import re
from discord.ui import View, Button
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
    bot.loop.create_task(find_match(bot))


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
        else:
            player1_score, player2_score = score2, score1

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
