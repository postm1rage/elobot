from config import bot, MODERATOR_ID
from db_manager import db_manager

def setup(bot):
    
    @bot.command()
    async def ban(ctx, nickname: str):
        """Забанить игрока"""
        if ctx.author.id != MODERATOR_ID:
            return await ctx.send("❌ Только модератор может использовать эту команду")
            
        player = db_manager.fetchone(
            "players",
            "SELECT playername FROM players WHERE playername = ?",
            (nickname,)
        )
        
        if not player:
            return await ctx.send(f"❌ Игрок с ником '{nickname}' не найден")
            
        try:
            db_manager.execute(
                "players",
                "UPDATE players SET isbanned = 1 WHERE playername = ?",
                (nickname,)
            )
            await ctx.send(f"✅ Игрок {nickname} успешно забанен")
            print(f"[BAN] Игрок {nickname} забанен модератором {ctx.author.name}")
        except Exception as e:
            await ctx.send(f"❌ Ошибка при бане игрока: {e}")
            print(f"[BAN ERROR] {e}")

    @bot.command()
    async def unban(ctx, nickname: str):
        """Разбанить игрока"""
        if ctx.author.id != MODERATOR_ID:
            return await ctx.send("❌ Только модератор может использовать эту команду")
            
        player = db_manager.fetchone(
            "players",
            "SELECT playername FROM players WHERE playername = ?",
            (nickname,)
        )
        
        if not player:
            return await ctx.send(f"❌ Игрок с ником '{nickname}' не найден")
            
        try:
            db_manager.execute(
                "players",
                "UPDATE players SET isbanned = 0 WHERE playername = ?",
                (nickname,)
            )
            await ctx.send(f"✅ Игрок {nickname} успешно разбанен")
            print(f"[UNBAN] Игрок {nickname} разбанен модератором {ctx.author.name}")
        except Exception as e:
            await ctx.send(f"❌ Ошибка при разбане игрока: {e}")
            print(f"[UNBAN ERROR] {e}")