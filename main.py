import logging
from dotenv import load_dotenv
import os
from config import bot, db
from verification import setup_verified_role, setup as setup_verification
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

        # Проверяем каналы
        verify_channel = discord.utils.get(
            guild.text_channels, name="elobot-verify"
        )
        results_channel = discord.utils.get(
            guild.text_channels, name="elobot-logs"
        )

        if not verify_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала 'elobot-verify'")
        if not results_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала 'elobot-logs'")

@bot.command()
async def playerinfo(ctx, nickname: str):
    """Показывает информацию об игроке"""
    c = db.cursor()
    c.execute(
        """
    SELECT playerid, playername, discordid, currentelo, wins, losses, ties, currentmatches
    FROM players
    WHERE playername = ?
    """,
        (nickname,),
    )

    player = c.fetchone()

    if player:
        # Убедимся, что все значения имеют дефолтные значения, если они NULL
        player_data = {
            "id": player[0],
            "name": player[1],
            "discord_id": player[2],
            "elo": player[3] if player[3] is not None else 1000,
            "wins": player[4] if player[4] is not None else 0,
            "losses": player[5] if player[5] is not None else 0,
            "ties": player[6] if player[6] is not None else 0,
            "matches": player[7] if player[7] is not None else 0,
        }

        embed = discord.Embed(
            title=f"Информация об игроке {player_data['name']}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="ID", value=player_data["id"], inline=True)
        embed.add_field(name="Discord ID", value=player_data["discord_id"], inline=True)
        embed.add_field(name="ELO", value=player_data["elo"], inline=True)
        embed.add_field(name="Победы", value=player_data["wins"], inline=True)
        embed.add_field(name="Поражения", value=player_data["losses"], inline=True)
        embed.add_field(name="Ничьи", value=player_data["ties"], inline=True)
        embed.add_field(name="Всего матчей", value=player_data["matches"], inline=True)

        await ctx.send(embed=embed)
    else:
        await ctx.send(f"Игрок с ником '{nickname}' не найден")

# Закрытие базы данных при завершении
@bot.event
async def on_disconnect():
    db.close()
    print("База данных закрыта")

# Настройка модулей
setup_verification(bot)

bot.run(token, log_handler=handler, log_level=logging.DEBUG)