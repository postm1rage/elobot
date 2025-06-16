import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
import os
import sqlite3


# Инициализация базы данных
def init_db():
    db = sqlite3.connect("elobotplayers.db")
    c = db.cursor()

    c.execute(
        """
CREATE TABLE IF NOT EXISTS players (
    playerid INTEGER PRIMARY KEY AUTOINCREMENT,
    playername TEXT NOT NULL UNIQUE,
    discordid TEXT NOT NULL UNIQUE,
    leaderboardplace INTEGER DEFAULT 0,
    currentelo INTEGER DEFAULT 1000,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    ties INTEGER DEFAULT 0,
    currentmatches INTEGER DEFAULT 0
)
"""
    )

    db.commit()
    return db


# Инициализируем базу данных при старте
db = init_db()

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

# Конфигурация
VERIFY_CHANNEL_NAME = "elobot-verify"
RESULTS_CHANNEL_NAME = "elobot-logs"
MODERATOR_ID = 296821040221388801
VERIFIED_ROLE_NAME = "verified"
DEFAULT_ELO = 1000

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    intents=intents,
    command_prefix="!",
)


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
            view = VerifyView(message.id, message.guild.id, message.content.strip())

            await moderator.send(embed=embed, files=files, view=view)
        except Exception as e:
            print(f"Ошибка: {e}")

    await bot.process_commands(message)


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


bot.run(token, log_handler=handler, log_level=logging.DEBUG)
