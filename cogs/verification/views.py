import discord
from discord import ui
import sqlite3
from config import Config  # Импортируем конфигурацию
from database import db  # Импортируем базу данных


class VerifyView(ui.View):
    def __init__(self, bot, verify_message_id, guild_id, player_nickname):
        super().__init__(timeout=None)
        self.bot = bot  # Сохраняем экземпляр бота
        self.verify_message_id = verify_message_id
        self.guild_id = guild_id
        self.player_nickname = player_nickname

    async def add_player_to_db(self, discord_user):
        """Добавляет игрока в базу данных"""
        c = db.conn.cursor()
        try:
            c.execute(
                "INSERT INTO players (playername, discordid) VALUES (?, ?)",
                (self.player_nickname, str(discord_user.id)),
            )
            db.conn.commit()
            return True
        except sqlite3.IntegrityError as e:
            print(f"Ошибка при добавлении игрока: {e}")
            return False

    async def send_result(self, guild, user_to_verify, success: bool):
        """Отправляет результат верификации в канал логов"""
        results_channel = discord.utils.get(
            guild.text_channels, name=Config.RESULTS_CHANNEL_NAME
        )

        if not results_channel:
            try:
                await guild.owner.send(
                    f"⚠️ Канал '{Config.RESULTS_CHANNEL_NAME}' не найден!"
                )
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
                verified_role = discord.utils.get(
                    guild.roles, name=Config.VERIFIED_ROLE_NAME
                )
                if verified_role:
                    await user_to_verify.add_roles(verified_role)

                embed = discord.Embed(
                    title="✅ Верификация пройдена",
                    description=(
                        f"{user_to_verify.mention} успешно верифицирован\n"
                        f"Никнейм: {self.player_nickname}\n"
                        f"Начальный ELO: {Config.DEFAULT_ELO}"
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

    @ui.button(label="Верифицировать", style=discord.ButtonStyle.green)
    async def verify_accept(self, interaction: discord.Interaction, button: ui.Button):
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message(
                "❌ Сервер не найден", ephemeral=True
            )
            return

        try:
            verify_channel = discord.utils.get(
                guild.text_channels, name=Config.VERIFY_CHANNEL_NAME
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

    @ui.button(label="Отклонить", style=discord.ButtonStyle.red)
    async def verify_reject(self, interaction: discord.Interaction, button: ui.Button):
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message(
                "❌ Сервер не найден", ephemeral=True
            )
            return

        try:
            verify_channel = discord.utils.get(
                guild.text_channels, name=Config.VERIFY_CHANNEL_NAME
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
