import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
import os
from config import Config
from database import db
import atexit

load_dotenv()

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    intents=intents,
    command_prefix="!",
)

async def setup_hook():
    await bot.add_cog(VerificationCog(bot))

@bot.event
async def on_ready():
    print(f"Бот {bot.user.name} запущен!")
    await setup_hook()

bot.run(os.getenv("DISCORD_TOKEN"), log_handler=handler, log_level=logging.DEBUG)
