from dotenv import load_dotenv
from role_manager import setup_role_manager
from nickname_updater import setup_nickname_updater
from config import (
    bot,
    MODERATOR_ID,
)
from db_manager import db_manager


def setup(bot):
    global global_bot
    global_bot = bot

    @bot.command()
    async def ban(ctx, nickname: str):
        """Забанить игрока"""
        if ctx.author.id == MODERATOR_ID:
            player = db_manager.fetchone(
                "players",
                """
                SELECT playername
                FROM players
                """,
                (nickname,),
            )
            if player:
                if player["playername"] == nickname:
                    db_manager.execute(
                        "players",
                        "UPDATE players SET banned = 1 WHERE playername = ?",
                        (nickname,),
                    )
                    await ctx.send(f"Игрок {nickname} забанен")

    @bot.command()
    async def unban(ctx, nickname: str):
        """Разбанить игрока"""
        if ctx.author.id == MODERATOR_ID:
            player = db_manager.fetchone(
                "players",
                """
                SELECT playername
                FROM players
                """,
                (nickname,),
            )
            if player:
                if player["playername"] == nickname:
                    db_manager.execute(
                        "players",
                        "UPDATE players SET banned = 0 WHERE playername = ?",
                        (nickname,),
                    )
                    await ctx.send(f"Игрок {nickname} разбанен")


### запрет использовать команды, если игрок в бане


async def check_ban(ctx):
    """Проверяет, забанен ли пользователь"""
    player = db_manager.fetchone(
        "players",
        "SELECT isbanned FROM players WHERE discordid = ?",
        (str(ctx.author.id),),
    )

    if player and player[0] == 1:  # Если isbanned == 1
        await ctx.send("⛔ Вы забанены и не можете использовать команды бота.")
        return True
    return False


@bot.check
async def globally_check_ban(ctx):
    if ctx.channel.name in ["elobot-verify", "elobot-logs"]:
        return True
    return not await check_ban(ctx)
