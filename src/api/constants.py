from os import getenv

if __debug__:
    from dotenv import load_dotenv

    load_dotenv()

DATABASE_NAME = getenv("DATABASE_NAME")
TOKEN = getenv("TOKEN")
BOT_NAME = getenv("BOT_NAME")

if DATABASE_NAME is None:
    raise ValueError("The 'DATABASE_NAME' environment variable must be set.")

if TOKEN is None:
    raise ValueError("The 'TOKEN' environment variable must be set.")

if BOT_NAME is None:
    raise ValueError("The 'BOT_NAME' environment variable must be set.")
