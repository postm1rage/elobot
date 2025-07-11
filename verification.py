import discord
from discord.ui import View, Button
from db_manager import db_manager
import logging
from role_manager import assign_role  # Импорт функции выдачи роли
from config import MODERATOR_ID

# Настройка логгера
logger = logging.getLogger("verification")


class VerifyView(View):
    def __init__(self, bot_instance, verify_message_id, guild_id, player_nickname):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self.custom_id = f"verify_view_{player_nickname}_{verify_message_id}"
        self.verify_message_id = verify_message_id
        self.guild_id = guild_id
        self.player_nickname = player_nickname

    async def add_player_to_db(self, discord_user):
        """Добавляет игрока в базу данных"""
        try:
            db_manager.execute(
                "players",
                """
                INSERT INTO players (playername, discordid, currentelo, 
                                    elo_station5f, elo_mots, elo_12min,
                                    wins, losses, ties, wins_station5f, 
                                    losses_station5f, ties_station5f,
                                    wins_mots, losses_mots, ties_mots,
                                    wins_12min, losses_12min, ties_12min, isbanned)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.player_nickname,
                    str(discord_user.id),
                    1000,
                    1000,
                    1000,
                    1000,  # ELO значения
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,  # Статистика
                    0,  # isbanned
                ),
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления игрока: {e}")
            return False

    async def send_result(self, guild, user_to_verify, success: bool):
        """Отправляет результат верификации в канал логов"""
        try:
            logs_channel = discord.utils.get(guild.text_channels, name="elobot-logs")
            if not logs_channel:
                logger.warning(f"Канал 'elobot-logs' не найден на сервере {guild.name}")
                return

            if success:
                embed = discord.Embed(
                    title="✅ Верификация пройдена",
                    description=(
                        f"{user_to_verify.mention} успешно верифицирован\n"
                        f"Никнейм: {self.player_nickname}\n"
                        f"Начальный ELO: 1000"
                    ),
                    color=discord.Color.green(),
                )

                # Вызываем выдачу роли
                await assign_role(user_to_verify)

                # Отправляем кастомное событие
                self.bot.dispatch("verification_complete", user_to_verify, guild)
            else:
                embed = discord.Embed(
                    title="❌ Верификация отклонена",
                    description=f"{user_to_verify.mention} не прошел верификацию",
                    color=discord.Color.red(),
                )

            await logs_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Ошибка при отправке результата верификации: {e}")

    @discord.ui.button(label="Верифицировать", style=discord.ButtonStyle.green)
    async def verify_accept(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message(
                "❌ Сервер не найден", ephemeral=True
            )
            return

        try:
            verify_channel = discord.utils.get(
                guild.text_channels, name="elobot-verify"
            )
            verify_message = await verify_channel.fetch_message(self.verify_message_id)
            user_to_verify = verify_message.author

            if not await self.add_player_to_db(user_to_verify):
                await interaction.response.send_message(
                    "❌ Ошибка добавления в базу данных", ephemeral=True
                )
                return

            await self.send_result(guild, user_to_verify, success=True)
            await interaction.message.edit(
                content=f"✅ {user_to_verify.display_name} верифицирован как {self.player_nickname}",
                view=None,
            )
            await interaction.response.defer()
        except Exception as e:
            logger.error(f"Ошибка в verify_accept: {e}")
            await interaction.response.send_message(
                f"❌ Ошибка: {str(e)}", ephemeral=True
            )

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red)
    async def verify_reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message(
                "❌ Сервер не найден", ephemeral=True
            )
            return

        try:
            verify_channel = discord.utils.get(
                guild.text_channels, name="elobot-verify"
            )
            verify_message = await verify_channel.fetch_message(self.verify_message_id)
            user_to_verify = verify_message.author

            await self.send_result(guild, user_to_verify, success=False)
            await interaction.message.edit(
                content=f"❌ {user_to_verify.display_name} отклонён",
                view=None,
            )
            await interaction.response.defer()
        except Exception as e:
            logger.error(f"Ошибка в verify_reject: {e}")
            await interaction.response.send_message(
                f"❌ Ошибка: {str(e)}", ephemeral=True
            )


async def setup_verified_role(guild):
    """Создаёт роль verified если её нет"""
    verified_role = discord.utils.get(guild.roles, name="Verified")

    if not verified_role:
        try:
            verified_role = await guild.create_role(
                name="Verified",
                color=discord.Color.green(),
                reason="Автоматическое создание роли для верификации",
            )
            logger.info(f"Создана роль 'Verified' на сервере {guild.name}")
        except discord.Forbidden:
            logger.error(f"Нет прав для создания роли на сервере {guild.name}")
    return verified_role


def setup(bot):
    @bot.event
    async def on_message(message):
        if message.author.bot:
            return await bot.process_commands(message)

        # Пропускаем команды
        if message.content.startswith(("!", ".")):
            await bot.process_commands(message)
            return

        if message.channel.name == "elobot-verify":
            if not message.content.strip():
                await message.delete()
                return

            try:
                # Проверка 1: Наличие скриншота
                if not message.attachments:
                    logs_channel = discord.utils.get(
                        message.guild.text_channels, name="elobot-logs"
                    )
                    if logs_channel:
                        embed = discord.Embed(
                            title="❌ Верификация отклонена (автоматически)",
                            description=(
                                f"Игрок {message.author.mention}\n"
                                f"Причина: Отсутствует скриншот\n"
                                f"Никнейм: {message.content}"
                            ),
                            color=discord.Color.red(),
                        )
                        await logs_channel.send(embed=embed)
                    await message.delete()
                    return

                # Проверка 2: Существующий Discord ID
                if db_manager.execute(
                    "players",
                    "SELECT 1 FROM players WHERE discordid = ?",
                    (str(message.author.id),),
                ).fetchone():
                    logs_channel = discord.utils.get(
                        message.guild.text_channels, name="elobot-logs"
                    )
                    if logs_channel:
                        embed = discord.Embed(
                            title="❌ Верификация отклонена (автоматически)",
                            description=(
                                f"Игрок {message.author.mention}\n"
                                f"Причина: Discord ID уже зарегистрирован\n"
                                f"Никнейм: {message.content}"
                            ),
                            color=discord.Color.red(),
                        )
                        await logs_channel.send(embed=embed)
                    await message.delete()
                    return

                # Проверка 3: Существующее имя игрока
                if db_manager.execute(
                    "players",
                    "SELECT 1 FROM players WHERE playername = ?",
                    (message.content.strip(),),
                ).fetchone():
                    logs_channel = discord.utils.get(
                        message.guild.text_channels, name="elobot-logs"
                    )
                    if logs_channel:
                        embed = discord.Embed(
                            title="❌ Верификация отклонена (автоматически)",
                            description=(
                                f"Игрок {message.author.mention}\n"
                                f"Причина: Никнейм уже занят\n"
                                f"Никнейм: {message.content}"
                            ),
                            color=discord.Color.red(),
                        )
                        await logs_channel.send(embed=embed)
                    await message.delete()
                    return

                # Если все проверки пройдены - отправляем модератору
                moderator = await bot.fetch_user(
                    MODERATOR_ID
                )  # Замените на ID модератора
                embed = discord.Embed(
                    title="🆕 Новая заявка на верификацию",
                    description=(
                        f"**Никнейм:** {message.content}\n"
                        f"**Отправитель:** {message.author.mention}"
                    ),
                    color=discord.Color.blue(),
                )
                embed.set_footer(text=f"ID: {message.id}")

                files = [
                    await attachment.to_file() for attachment in message.attachments
                ]
                view = VerifyView(
                    bot, message.id, message.guild.id, message.content.strip()
                )

                await moderator.send(embed=embed, files=files, view=view)

            except Exception as e:
                logger.error(f"Ошибка обработки верификации: {e}")
                try:
                    logs_channel = discord.utils.get(
                        message.guild.text_channels, name="elobot-logs"
                    )
                    if logs_channel:
                        await logs_channel.send(
                            f"⚠️ Ошибка при обработке верификации: {str(e)}"
                        )
                except:
                    pass

            await bot.process_commands(message)
            return

        await bot.process_commands(message)
