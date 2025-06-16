import discord
from discord.ext import commands
from discord import ui
import sqlite3
from config import Config
from database import db
from .views import VerifyView
from .utils import setup_verified_role

class VerificationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        """Проверка и создание необходимых ролей/каналов при запуске"""
        for guild in self.bot.guilds:
            await setup_verified_role(guild)
            
            # Проверяем каналы
            verify_channel = discord.utils.get(
                guild.text_channels, name=Config.VERIFY_CHANNEL_NAME
            )
            results_channel = discord.utils.get(
                guild.text_channels, name=Config.RESULTS_CHANNEL_NAME
            )

            if not verify_channel:
                print(f"⚠️ На сервере '{guild.name}' нет канала '{Config.VERIFY_CHANNEL_NAME}'")
            if not results_channel:
                print(f"⚠️ На сервере '{guild.name}' нет канала '{Config.RESULTS_CHANNEL_NAME}'")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Обработка сообщений в канале верификации"""
        if message.author.bot:
            return

        if message.channel.name == Config.VERIFY_CHANNEL_NAME:
            if not message.content.strip():
                await message.delete()
                return

            try:
                # Проверка 1: Наличие скриншота
                if not message.attachments:
                    await self.handle_auto_reject(
                        message, 
                        "Отсутствует скриншот",
                        message.content
                    )
                    return

                # Проверка 2: Существующий Discord ID
                c = db.conn.cursor()
                c.execute("SELECT 1 FROM players WHERE discordid = ?", (str(message.author.id),))
                if c.fetchone():
                    await self.handle_auto_reject(
                        message,
                        "Discord ID уже зарегистрирован",
                        message.content
                    )
                    return

                # Проверка 3: Существующее имя игрока
                c.execute("SELECT 1 FROM players WHERE playername = ?", (message.content.strip(),))
                if c.fetchone():
                    await self.handle_auto_reject(
                        message,
                        "Никнейм уже занят",
                        message.content
                    )
                    return

                # Если все проверки пройдены - отправляем модератору
                moderator = await self.bot.fetch_user(Config.MODERATOR_ID)
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
                        message.guild.text_channels, 
                        name=Config.RESULTS_CHANNEL_NAME
                    )
                    if results_channel:
                        await results_channel.send(f"⚠️ Ошибка при обработке верификации: {str(e)}")
                except:
                    pass

    async def handle_auto_reject(self, message, reason, nickname):
        """Обработка автоматического отклонения заявки"""
        results_channel = discord.utils.get(
            message.guild.text_channels, 
            name=Config.RESULTS_CHANNEL_NAME
        )
        if results_channel:
            embed = discord.Embed(
                title="❌ Верификация отклонена (автоматически)",
                description=(
                    f"Игрок {message.author.mention}\n"
                    f"Причина: {reason}\n"
                    f"Никнейм: {nickname}"
                ),
                color=discord.Color.red()
            )
            await results_channel.send(embed=embed)
        await message.delete()

    @commands.command()
    async def playerinfo(self, ctx, nickname: str):
        """Показывает информацию об игроке"""
        c = db.conn.cursor()
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
                "elo": player[3] if player[3] is not None else Config.DEFAULT_ELO,
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

async def setup(bot):
    await bot.add_cog(VerificationCog(bot))