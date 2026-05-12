import os
import asyncio
import logging
import sqlite3
import re
from datetime import datetime
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

# --- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PRICE_CHANNEL_ID = int(os.getenv("PRICE_CHANNEL_ID"))

session = AiohttpSession(proxy=PROXY_URL)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# --- DATABASE ---
def init_db():
    conn = sqlite3.connect("manager.db")
    cur = conn.cursor()
    # Чаты
    cur.execute("""CREATE TABLE IF NOT EXISTS chats (
        chat_id INTEGER PRIMARY KEY,
        username TEXT,
        pin_msg_id INTEGER,
        members INTEGER DEFAULT 0,
        joined_today INTEGER DEFAULT 0,
        left_today INTEGER DEFAULT 0
    )""")
    # Шаблоны постов в канале (чтобы не терять форматирование)
    cur.execute("""CREATE TABLE IF NOT EXISTS templates (
        msg_id INTEGER PRIMARY KEY,
        raw_text TEXT
    )""")
    conn.commit()
    conn.close()

# --- LOGIC ---

async def update_all_resources():
    """Обновляет все тексты везде, где только можно"""
    conn = sqlite3.connect("manager.db")
    cur = conn.cursor()
    cur.execute("SELECT username, members FROM chats")
    all_counts = {row[0]: row[1] for row in cur.fetchall()}
    
    # 1. Обновляем посты в канале прайсов
    cur.execute("SELECT msg_id, raw_text FROM templates")
    templates = cur.fetchall()
    for msg_id, text in templates:
        new_text = text
        for user, count in all_counts.items():
            # Ищем в тексте @username и меняем следующую за ним строку с цифрой
            pattern = rf"({user}\n\s*)(\d+)"
            new_text = re.sub(pattern, rf"\g<1>{count}", new_text)
        
        try:
            await bot.edit_message_text(chat_id=PRICE_CHANNEL_ID, message_id=msg_id, text=new_text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Ошибка правки канала: {e}")

    # 2. Обновляем закрепы в чатах
    # Здесь логика формирования списка всех чатов для закрепа
    list_text = "✨ 🙂🙂🙂🙂   🙂🙂🙂 ✨\n\nВсе мои пиар-чаты:\n\n"
    for user in all_counts.keys():
        list_text += f"🤩 {user}\n"
    list_text += "\nМой личный ТГК: https://t.me/+ThoDBS7OMkEzMTYy"

    cur.execute("SELECT chat_id, pin_msg_id FROM chats WHERE pin_msg_id IS NOT NULL")
    for c_id, p_id in cur.fetchall():
        try:
            await bot.edit_message_text(chat_id=c_id, message_id=p_id, text=list_text)
        except: pass
    
    conn.close()

# --- HANDLERS ---

@dp.message(Command("add_chat"), F.from_user.id == ADMIN_ID)
async def add_chat_cmd(message: types.Message):
    """Добавить чат в систему: /add_chat @username"""
    try:
        username = message.text.split()[1]
        chat = await bot.get_chat(username)
        count = await bot.get_chat_member_count(chat.id)
        
        conn = sqlite3.connect("manager.db")
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO chats (chat_id, username, members) VALUES (?, ?, ?)", 
                    (chat.id, username, count))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Чат {username} добавлен. Сейчас там {count} чел.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dp.message(Command("set_template"), F.from_user.id == ADMIN_ID)
async def set_template(message: types.Message):
    """Привязывает пост канала как шаблон. Ответь этой командой на пересланный из канала пост"""
    if not message.reply_to_message or not message.reply_to_message.forward_from_chat:
        return await message.answer("Ответь этой командой на сообщение из канала прайсов!")
    
    msg_id = message.reply_to_message.forward_from_message_id
    raw_text = message.reply_to_message.html_text # Сохраняем с HTML разметкой (прем эмодзи)
    
    conn = sqlite3.connect("manager.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO templates (msg_id, raw_text) VALUES (?, ?)", (msg_id, raw_text))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Пост #{msg_id} теперь обновляется автоматически.")

@dp.chat_member()
async def member_update(event: types.ChatMemberUpdated):
    """Событие входа/выхода"""
    conn = sqlite3.connect("manager.db")
    cur = conn.cursor()
    
    # Считаем приход/уход
    if event.new_chat_member.status in ["member", "administrator"]:
        cur.execute("UPDATE chats SET joined_today = joined_today + 1 WHERE chat_id = ?", (event.chat.id,))
    elif event.new_chat_member.status in ["left", "kicked"]:
        cur.execute("UPDATE chats SET left_today = left_today + 1 WHERE chat_id = ?", (event.chat.id,))
    
    # Обновляем счетчик
    new_count = await bot.get_chat_member_count(event.chat.id)
    cur.execute("UPDATE chats SET members = ? WHERE chat_id = ?", (new_count, event.chat.id))
    conn.commit()
    conn.close()
    
    # Запускаем обновление текстов (с небольшим анти-спам ожиданием)
    await update_all_resources()

async def send_daily_report():
    conn = sqlite3.connect("manager.db")
    cur = conn.cursor()
    cur.execute("SELECT username, members, joined_today, left_today FROM chats")
    data = cur.fetchall()
    
    report = f"📈 **Отчет за {datetime.now().strftime('%d.%m')}**\n\n"
    for row in data:
        report += f"{row[0]}: {row[1]} (За день: +{row[2]} | -{row[3]})\n"
    
    # Сброс дневной статистики
    cur.execute("UPDATE chats SET joined_today = 0, left_today = 0")
    conn.commit()
    conn.close()
    
    await bot.send_message(ADMIN_ID, report, parse_mode="Markdown")

# --- START ---
async def main():
    init_db()
    # Планировщик отчетов (каждый день в 23:59)
    scheduler.add_job(send_daily_report, 'cron', hour=23, minute=59)
    scheduler.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
