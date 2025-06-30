import discord
from discord.ext import tasks
from config import db
import logging

# Настройка логирования
logger = logging.getLogger("nickname_updater")


async def update_nickname(member, new_nickname):
    """Обновляет никнейм участника с обработкой ошибок"""
    try:
        # Проверяем, есть ли права на изменение
        if member.guild.me.guild_permissions.manage_nicknames:
            # Проверяем, не пытаемся ли изменить ник бота или владельца
            if member == member.guild.owner:
                logger.info(f"Пропуск владельца сервера: {member.display_name}")
                return

            if member.bot:
                logger.info(f"Пропуск бота: {member.display_name}")
                return

            # Проверяем, не превышает ли ник лимит длины
            if len(new_nickname) > 32:
                new_nickname = new_nickname[:29] + "..."
                logger.warning(f"Укорочен ник для {member.display_name}")

            # Обновляем ник, если он изменился
            if member.display_name != new_nickname:
                await member.edit(nick=new_nickname)
                logger.info(f"Обновлен ник: {member.display_name} -> {new_nickname}")
            else:
                logger.info(f"Ник не изменился: {new_nickname}")
        else:
            logger.warning(
                f"Нет прав на изменение ников на сервере {member.guild.name}"
            )
    except discord.Forbidden:
        logger.error(
            f"Ошибка прав выдачи роли для {member.display_name} на сервере {member.guild.name}"
        )
    except discord.HTTPException as e:
        logger.error(f"Ошибка HTTP при обновлении ника: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка: {e}")


def setup_nickname_updater(bot):
    """Инициализирует систему обновления ников"""

    @tasks.loop(minutes=10)
    async def update_all_nicknames():
        """Обновляет ники на всех серверах"""
        logger.info("Запуск периодического обновления ников")

        # Получаем всех игроков из БД
        c = db.cursor()
        c.execute("SELECT discordid, playername, currentelo FROM players")
        players = c.fetchall()

        # Создаем словарь {discordid: (nickname, elo)}
        player_data = {
            discord_id: (nickname, elo) for discord_id, nickname, elo in players
        }

        # Обходим все серверы, где есть бот
        for guild in bot.guilds:
            logger.info(f"Проверка сервера: {guild.name}")

            # Обходим всех участников сервера
            for member in guild.members:
                discord_id = str(member.id)

                # Если участник есть в базе игроков
                if discord_id in player_data:
                    nickname, elo = player_data[discord_id]
                    new_nick = f"{nickname} [{int(elo)}]"
                    await update_nickname(member, new_nick)

    @bot.event
    async def on_ready():
        """Запускаем задачу при старте бота"""
        if not update_all_nicknames.is_running():
            update_all_nicknames.start()
            logger.info("Задача обновления ников запущена")

    @bot.event
    async def on_member_join(member):
        """Обновляем ник при присоединении к серверу"""
        discord_id = str(member.id)

        # Получаем данные игрока из БД
        c = db.cursor()
        c.execute(
            "SELECT playername, currentelo FROM players WHERE discordid = ?",
            (discord_id,),
        )
        player = c.fetchone()

        if player:
            nickname, elo = player
            new_nick = f"{nickname} [{int(elo)}]"
            await update_nickname(member, new_nick)
