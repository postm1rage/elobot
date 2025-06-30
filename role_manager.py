import discord
from discord.ext import commands
from config import db
import logging

# Конфигурация ролей для серверов
# Формат: {guild_id: role_id}
ROLE_MAPPING = {
    # Ваш сервер
    1289560793523945645: 1387871400760447036,
    # Добавляйте другие серверы по мере необходимости
}

logger = logging.getLogger('role_manager')

async def assign_role(member):
    """Выдает роль пользователю на указанном сервере"""
    guild_id = member.guild.id
    
    # Проверяем, есть ли конфигурация для этого сервера
    if guild_id not in ROLE_MAPPING:
        logger.info(f"Для сервера {guild_id} ({member.guild.name}) нет конфигурации роли")
        return
        
    role_id = ROLE_MAPPING[guild_id]
    role = member.guild.get_role(role_id)
    
    if not role:
        logger.warning(f"Роль {role_id} не найдена на сервере {member.guild.name}")
        return
        
    try:
        # Проверяем, есть ли у пользователя роль
        if role not in member.roles:
            await member.add_roles(role)
            logger.info(f"Выдана роль {role.name} пользователю {member.display_name} на сервере {member.guild.name}")
        else:
            logger.debug(f"У пользователя {member.display_name} уже есть роль {role.name}")
    except discord.Forbidden:
        logger.error(f"Нет прав для выдачи роли на сервере {member.guild.name}")
    except discord.HTTPException as e:
        logger.error(f"Ошибка при выдаче роли: {e}")

def setup_role_manager(bot):
    """Инициализирует систему управления ролями"""
    
    @bot.event
    async def on_member_join(member):
        """Выдает роль при присоединении к серверу"""
        # Проверяем, есть ли пользователь в системе
        c = db.cursor()
        c.execute(
            "SELECT 1 FROM players WHERE discordid = ?",
            (str(member.id),)
        )
        if c.fetchone():
            await assign_role(member)
    
    @bot.event
    async def on_ready():
        """Выдает роли всем верифицированным пользователям при запуске"""
        logger.info("Проверка ролей для всех серверов")
        
        # Получаем всех верифицированных игроков
        c = db.cursor()
        c.execute("SELECT discordid FROM players")
        verified_users = [row[0] for row in c.fetchall()]
        
        # Обходим все серверы
        for guild in bot.guilds:
            # Пропускаем серверы без конфигурации
            if guild.id not in ROLE_MAPPING:
                continue
                
            # Получаем роль для сервера
            role_id = ROLE_MAPPING[guild.id]
            role = guild.get_role(role_id)
            
            if not role:
                logger.warning(f"Роль {role_id} не найдена на сервере {guild.name}")
                continue
                
            # Обходим всех участников сервера
            for member in guild.members:
                # Проверяем, верифицирован ли пользователь
                if str(member.id) in verified_users:
                    try:
                        # Проверяем наличие роли
                        if role not in member.roles:
                            await member.add_roles(role)
                            logger.info(f"Выдана роль {role.name} пользователю {member.display_name} на сервере {guild.name}")
                    except discord.Forbidden:
                        logger.error(f"Нет прав для выдачи роли пользователю {member.display_name}")
                    except discord.HTTPException as e:
                        logger.error(f"Ошибка HTTP: {e}")

    @bot.event
    async def on_verification_complete(user, guild):
        """Кастомное событие при завершении верификации"""
        # Получаем объект участника
        member = guild.get_member(user.id)
        if member:
            await assign_role(member)