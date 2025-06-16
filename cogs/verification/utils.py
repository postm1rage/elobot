import discord
from config import Config

async def setup_verified_role(guild):
    """Создаёт роль verified если её нет"""
    if not discord.utils.get(guild.roles, name=Config.VERIFIED_ROLE_NAME):
        try:
            await guild.create_role(
                name=Config.VERIFIED_ROLE_NAME,
                color=discord.Color.default(),
                reason="Автоматическое создание роли для верификации",
            )
            print(f"Создана роль '{Config.VERIFIED_ROLE_NAME}' на сервере '{guild.name}'")
        except discord.Forbidden:
            print(f"⚠️ Нет прав для создания роли на сервере '{guild.name}'")