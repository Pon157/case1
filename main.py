import os
import asyncio
import logging
import sqlite3
import re
import sys
from datetime import datetime
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настраиваем логирование, чтобы видеть всё в pm2 logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)

load_dotenv()

# --- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PRICE_CHANNEL_ID = int(os.getenv("PRICE_CHANNEL_ID"))

# Инициализация сессии и бота
session = AiohttpSession(proxy=PROXY_URL)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# --- DATABASE ---
def init_db():
    conn = sqlite3.connect("manager.db")
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS chats (
        chat_id INTEGER PRIMARY KEY,
        username TEXT,
        pin_msg_id INTEGER,
        members INTEGER DEFAULT 0,
        joined_today INTEGER DEFAULT 0,
        left_today INTEGER DEFAULT 0
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS templates (
        msg_id INTEGER PRIMARY KEY,
        raw_text TEXT
    )""")
    conn.commit()
    conn.close()
    logging.info("База данных инициализирована.")

# --- LOGIC ---
async def update_all_resources():
    logging.info("Обновление ресурсов...")
    conn = sqlite3.connect("manager.db")
    cur = conn.cursor()
    cur.execute("SELECT username, members FROM chats")
    all_counts = {row[0]: row[1] for row in cur.fetchall()}
    
    cur.execute("SELECT msg_id, raw_text FROM templates")
    templates = cur.fetchall()
    
    for msg_id, text in templates:
        new_text = text
        for user, count in all_counts.items():
            # Регулярка для поиска юзернейма и числа под ним
            pattern = rf"({re.escape(user)}\n\s*)(\d+)"
            new_text = re.sub(pattern, rf"\g<1>{count}", new_text)
        
        try:
            await bot.edit_message_text(
                chat_id=PRICE_CHANNEL_ID, 
                message_id=msg_id, 
                text=new_text, 
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Ошибка правки канала (сообщение {msg_id}): {e}")

    # Обновление закрепов (если нужно)
    cur.execute("SELECT chat_id, pin_msg_id FROM chats WHERE pin_msg_id IS NOT NULL")
    pins = cur.fetchall()
    if pins:
        list_text = "✨ 🙂🙂🙂🙂   🙂🙂🙂 ✨\n\nВсе мои пиар-чаты:\n\n"
        for user in all_counts.keys():
            list_text += f"🤩 {user}\n"
        list_text += "\nМой личный ТГК: https://t.me/+ThoDBS7OMkEzMTYy"
        
        for c_id, p_id in pins:
            try:
                await bot.edit_message_text(chat_id=c_id, message_id=p_id, text=list_text)
            except Exception:
                pass
    
    conn.close()

# --- HANDLERS ---
@dp.message(Command("add_chat"), F.from_user.id == ADMIN_ID)
async def add_chat_cmd(message: types.Message):
    try:
        parts = message.text.split()
        if len(parts) < 2:
            return await message.answer("Формат: /add_chat @username")
        username = parts[1]
        chat = await bot.get_chat(username)
        count = await bot.get_chat_member_count(chat.id)
        
        conn = sqlite3.connect("manager.db")
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO chats (chat_id, username, members) VALUES (?, ?, ?)", 
                    (chat.id, username, count))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Чат {username} добавлен. Участников: {count}")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dp.message(Command("set_template"), F.from_user.id == ADMIN_ID)
async def set_template(message: types.Message):
    if not message.reply_to_message or not message.reply_to_message.forward_from_chat:
        return await message.answer("Ответь этой командой на пересланное сообщение из канала!")
    
    msg_id = message.reply_to_message.forward_from_message_id
    raw_text = message.reply_to_message.html_text
    
    conn = sqlite3.connect("manager.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO templates (msg_id, raw_text) VALUES (?, ?)", (msg_id, raw_text))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Шаблон для сообщения #{msg_id} сохранен.")

@dp.chat_member()
async def member_update(event: types.ChatMemberUpdated):
    conn = sqlite3.connect("manager.db")
    cur = conn.cursor()
    if event.new_chat_member.status in ["member", "administrator"]:
        cur.execute("UPDATE chats SET joined_today = joined_today + 1 WHERE chat_id = ?", (event.chat.id,))
    elif event.new_chat_member.status in ["left", "kicked"]:
        cur.execute("UPDATE chats SET left_today = left_today + 1 WHERE chat_id = ?", (event.chat.id,))
    
    new_count = await bot.get_chat_member_count(event.chat.id)
    cur.execute("UPDATE chats SET members = ? WHERE chat_id = ?", (new_count, event.chat.id))
    conn.commit()
    conn.close()
    await update_all_resources()

async def send_daily_report():
    conn = sqlite3.connect("manager.db")
    cur = conn.cursor()
    cur.execute("SELECT username, members, joined_today, left_today FROM chats")
    data = cur.fetchall()
    if not data: return
    
    report = f"📈 **Отчет за {datetime.now().strftime('%d.%m')}**\n\n"
    for row in data:
        report += f"{row[0]}: {row[1]} (+{row[2]} | -{row[3]})\n"
    
    cur.execute("UPDATE chats SET joined_today = 0, left_today = 0")
    conn.commit()
    conn.close()
    await bot.send_message(ADMIN_ID, report, parse_mode="Markdown")

# --- START ---
async def main():
    init_db()
    scheduler.add_job(send_daily_report, 'cron', hour=23, minute=59)
    scheduler.start()
    logging.info("Бот запущен и готов к работе.")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
    except Exception as e:
        logging.critical(f"Ошибка: {e}", exc_info=True)
