import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
import os

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

# Конфигурация
VERIFY_CHANNEL_NAME = "elobot-verify"
RESULTS_CHANNEL_NAME = "elobot-logs"
MODERATOR_ID = 296821040221388801
VERIFIED_ROLE_NAME = "verified"  # Название роли для верифицированных

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    intents=intents,
    command_prefix="!",
)


class VerifyView(discord.ui.View):
    def __init__(self, verify_message_id, guild_id):
        super().__init__(timeout=None)
        self.verify_message_id = verify_message_id
        self.guild_id = guild_id

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
                # Выдаём роль verified
                verified_role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
                if verified_role:
                    await user_to_verify.add_roles(verified_role)

                embed = discord.Embed(
                    title="✅ Верификация пройдена",
                    description=f"{user_to_verify.mention} успешно верифицирован",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="❌ Верификация отклонена",
                    description=f"{user_to_verify.mention} не прошел верификацию",
                    color=discord.Color.red(),
                )

            await results_channel.send(embed=embed)
        except discord.Forbidden:
            try:
                await guild.owner.send(
                    f"⚠️ Нет прав для выдачи роли или отправки сообщений!"
                )
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
                content=f"✅ {user_to_verify.display_name} верифицирован", view=None
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


@bot.event
async def on_ready():
    print(f"Бот {bot.user.name} запущен!")

    # Проверяем и создаём необходимые роли/каналы
    for guild in bot.guilds:
        # Создаём роль verified
        await setup_verified_role(guild)

        # Проверяем каналы
        verify_channel = discord.utils.get(
            guild.text_channels, name=VERIFY_CHANNEL_NAME
        )
        results_channel = discord.utils.get(
            guild.text_channels, name=RESULTS_CHANNEL_NAME
        )

        if not verify_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала '{VERIFY_CHANNEL_NAME}'")
        if not results_channel:
            print(f"⚠️ На сервере '{guild.name}' нет канала '{RESULTS_CHANNEL_NAME}'")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.name == VERIFY_CHANNEL_NAME:
        if not message.content.strip():
            await message.delete()
            return

        try:
            moderator = await bot.fetch_user(MODERATOR_ID)

            embed = discord.Embed(
                title="Новая заявка на верификацию",
                description=f"**Никнейм:** {message.content}\n**Отправитель:** {message.author.mention}",
                color=discord.Color.blue(),
            )
            embed.set_footer(text=f"ID: {message.id}")

            files = [await attachment.to_file() for attachment in message.attachments]
            view = VerifyView(message.id, message.guild.id)

            await moderator.send(embed=embed, files=files, view=view)
        except Exception as e:
            print(f"Ошибка: {e}")

    await bot.process_commands(message)


bot.run(token, log_handler=handler, log_level=logging.DEBUG)
