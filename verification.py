import discord
from config import db, bot, VERIFY_CHANNEL_NAME, RESULTS_CHANNEL_NAME, MODERATOR_ID, VERIFIED_ROLE_NAME, DEFAULT_ELO
import sqlite3

class VerifyView(discord.ui.View):
    def __init__(self, verify_message_id, guild_id, player_nickname):
        super().__init__(timeout=None)
        self.verify_message_id = verify_message_id
        self.guild_id = guild_id
        self.player_nickname = player_nickname

    async def add_player_to_db(self, discord_user):
        """Добавляет игрока в базу данных"""
        c = db.cursor()
        try:
            c.execute(
                """
            INSERT INTO players (playername, discordid)
            VALUES (?, ?)
            """,
                (self.player_nickname, str(discord_user.id)),
            )
            db.commit()
            return True
        except sqlite3.IntegrityError as e:
            print(f"Ошибка при добавлении игрока: {e}")
            return False

    async def send_result(self, guild, user_to_verify, success: bool):
        """Отправляет результат верификации в канал логов"""
        results_channel = discord.utils.get(
            guild.text_channels, name=RESULTS_CHANNEL_NAME
        )

        if not results_channel:
            try:
                await guild.owner.send(f"⚠️ Канал '{RESULTS_CHANNEL_NAME}' не найден!")
            except:
                pass
            return

        try:
            if success:
                # Добавляем игрока в базу данных
                db_success = await self.add_player_to_db(user_to_verify)
                if not db_success:
                    raise Exception("Не удалось добавить игрока в базу данных")

                # Выдаём роль verified
                verified_role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
                if verified_role:
                    await user_to_verify.add_roles(verified_role)

                embed = discord.Embed(
                    title="✅ Верификация пройдена",
                    description=(
                        f"{user_to_verify.mention} успешно верифицирован\n"
                        f"Никнейм: {self.player_nickname}\n"
                        f"Начальный ELO: {DEFAULT_ELO}"
                    ),
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="❌ Верификация отклонена",
                    description=f"{user_to_verify.mention} не прошел верификацию",
                    color=discord.Color.red(),
                )

            await results_channel.send(embed=embed)
        except Exception as e:
            print(f"Ошибка: {e}")
            try:
                await guild.owner.send(f"⚠️ Ошибка при верификации: {str(e)}")
            except:
                pass

    @discord.ui.button(label="Верифицировать", style=discord.ButtonStyle.green)
    async def verify_accept(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message(
                "❌ Сервер не найден", ephemeral=True
            )
            return

        try:
            verify_channel = discord.utils.get(
                guild.text_channels, name=VERIFY_CHANNEL_NAME
            )
            verify_message = await verify_channel.fetch_message(self.verify_message_id)
            user_to_verify = verify_message.author

            await self.send_result(guild, user_to_verify, success=True)
            await interaction.message.edit(
                content=f"✅ {user_to_verify.display_name} верифицирован как {self.player_nickname}",
                view=None,
            )
            await interaction.response.defer()

        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка: {str(e)}", ephemeral=True
            )

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red)
    async def verify_reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message(
                "❌ Сервер не найден", ephemeral=True
            )
            return

        try:
            verify_channel = discord.utils.get(
                guild.text_channels, name=VERIFY_CHANNEL_NAME
            )
            verify_message = await verify_channel.fetch_message(self.verify_message_id)
            user_to_verify = verify_message.author

            await self.send_result(guild, user_to_verify, success=False)
            await interaction.message.edit(
                content=f"❌ {user_to_verify.display_name} отклонён", view=None
            )
            await interaction.response.defer()

        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка: {str(e)}", ephemeral=True
            )

async def setup_verified_role(guild):
    """Создаёт роль verified если её нет"""
    if not discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME):
        try:
            await guild.create_role(
                name=VERIFIED_ROLE_NAME,
                color=discord.Color.default(),
                reason="Автоматическое создание роли для верификации",
            )
            print(f"Создана роль '{VERIFIED_ROLE_NAME}' на сервере '{guild.name}'")
        except discord.Forbidden:
            print(f"⚠️ Нет прав для создания роли на сервере '{guild.name}'")

def setup(bot):
    @bot.event
    async def handle_verification(message):
        if message.channel.name == VERIFY_CHANNEL_NAME and not message.author.bot:

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

        await bot.process_commands(message)
        bot.add_listener(handle_verification, 'on_message')