import logging
import asyncio
import re
import uuid
import os
import aiosqlite
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
)
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"

# ID твоего канала (добавил -100, так требует API для приватных каналов)
CHANNEL_ID = -1003496634656 
CHANNEL_URL = "https://t.me/RavionScripts" # Ссылка на переходник или основной канал
BOT_USERNAME = "RavionAdministrator_bot"
WATERMARK = "https://t.me/RavionScripts"

# ID Админов (без паролей, доступ по ID)
ADMINS = {7637946765, 6510703948}

# --- НАСТРОЙКА ПУТИ К БАЗЕ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "scripts_data.db")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
scheduled_posts: Dict[str, Dict[str, Any]] = {}
instruction_messages: Dict[int, int] = {}

# --- СОСТОЯНИЯ (FSM) ---
class Form(StatesGroup):
    waiting_content = State()
    waiting_time = State()

class BroadcastState(StatesGroup):
    waiting_message = State()
    confirm_send = State()

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица скриптов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scripts (
                id TEXT PRIMARY KEY,
                game_name TEXT,
                code TEXT,
                is_key BOOLEAN,
                created_at TIMESTAMP,
                views INTEGER DEFAULT 0
            )
        """)
        # Таблица пользователей (для рассылки)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                joined_at TIMESTAMP
            )
        """)
        await db.commit()
    logger.info(f"База данных подключена: {DB_PATH}")

async def add_user_to_db(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, joined_at) VALUES (?, ?)",
            (user_id, datetime.now())
        )
        await db.commit()

async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def add_script_to_db(game_name: str, code: str, is_key: bool) -> str:
    unique_id = str(uuid.uuid4())[:8]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO scripts (id, game_name, code, is_key, created_at) VALUES (?, ?, ?, ?, ?)",
            (unique_id, game_name, code, is_key, datetime.now())
        )
        await db.commit()
    return unique_id

async def get_script_from_db(script_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT game_name, code, is_key, views FROM scripts WHERE id = ?", (script_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                await db.execute("UPDATE scripts SET views = views + 1 WHERE id = ?", (script_id,))
                await db.commit()
                return {'game': row[0], 'code': row[1], 'key': row[2], 'views': row[3]}
    return None

async def get_db_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        scripts_count = await db.execute("SELECT COUNT(*) FROM scripts")
        scripts_c = await scripts_count.fetchone()
        
        views_count = await db.execute("SELECT SUM(views) FROM scripts")
        views_c = await views_count.fetchone()
        
        users_count = await db.execute("SELECT COUNT(*) FROM users")
        users_c = await users_count.fetchone()
        
        return scripts_c[0], (views_c[0] or 0), users_c[0]

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

async def check_subscription(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['creator', 'administrator', 'member']
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        # Если бот не админ в канале или ошибка, временно пускаем (или можно False)
        return False 

def html_escape(text: str) -> str:
    if not text: return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def parse_content(raw_text: str) -> Dict[str, Any]:
    lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
    res = {'game': '🎮 Game', 'desc': '', 'key': False, 'code': []}
    
    if not lines: return res
    res['game'] = lines[0]
    
    code_found = False
    desc_lines = []
    
    for line in lines[1:]:
        low = line.lower()
        if '#key' in low or 'key+' in low: res['key'] = True; continue
        if '#nokey' in low or 'key-' in low or 'no key' in low: res['key'] = False; continue
            
        is_code = any(x in low for x in ['loadstring', 'game:', 'function', 'local ', 'getgenv', '```'])
        
        if not code_found and is_code:
            code_found = True
            clean = line.replace('```lua', '').replace('```', '')
            if 'game:HttpGet' in clean and WATERMARK not in clean:
                if clean.endswith('()'): clean = clean[:-2] + f'("{WATERMARK}")'
                elif clean.endswith('();'): clean = clean[:-3] + f'("{WATERMARK}");'
            res['code'].append(clean)
        elif code_found:
            res['code'].append(line.replace('```', ''))
        else:
            if not line.startswith('#'): desc_lines.append(line)
    
    res['desc'] = '\n'.join(desc_lines)
    return res

def build_channel_post_text(data: Dict) -> str:
    game = html_escape(data['parsed']['game']).upper()
    desc = html_escape(data['parsed']['desc'])
    
    text = f"<b>━━━━━━━━━━━━━━━━━━━</b>\n🎮 <b>{game}</b>\n<b>━━━━━━━━━━━━━━━━━━━</b>\n\n"
    
    if desc: 
        quoted_desc = "\n".join(f"💬 {line}" for line in desc.split('\n'))
        text += f"<blockquote>{quoted_desc}</blockquote>\n\n"
        
    text += "🔐 <b>Требуется ключ</b>\n" if data['parsed']['key'] else "🔓 <b>Ключ не нужен</b>\n"
    text += "\n👇 <b>Нажми кнопку ниже, чтобы получить скрипт</b>"
        
    text += f"\n\n<b>━━━━━━━━━━━━━━━━━━━</b>\n📢 <a href='{CHANNEL_URL}'>Ravion Scripts</a>"
    return text

def parse_time(s: str) -> Optional[datetime]:
    now = datetime.now()
    s = s.lower().replace('  ', ' ').strip()
    try:
        if any(c in s for c in ['м', 'ч', 'm', 'h']):
            delta = 0
            if m := re.search(r'(\d+)\s*[чh]', s): delta += int(m.group(1)) * 60
            if m := re.search(r'(\d+)\s*[мm]', s): delta += int(m.group(1))
            return now + timedelta(minutes=delta) if delta > 0 else None
        if re.match(r'^\d{1,2}:\d{2}$', s):
            h, m = map(int, s.split(':'))
            t = now.replace(hour=h, minute=m, second=0)
            return t if t > now else t + timedelta(days=1)
    except: pass
    return None

# --- КЛАВИАТУРЫ ---

def kb_admin_main():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Новый пост"), KeyboardButton(text="📢 Рассылка")],
        [KeyboardButton(text="👤 Профиль Админа")]
    ], resize_keyboard=True)

def kb_preview():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="pub_now")],
        [InlineKeyboardButton(text="⏰ Отложить", callback_data="schedule")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def kb_queue_control(pid: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Выложить сейчас", callback_data=f"force_{pid}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{pid}")]
    ])

def kb_get_script(script_id: str):
    # Кнопка ведет в бота с параметром start
    url = f"https://t.me/{BOT_USERNAME}?start={script_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 ПОЛУЧИТЬ СКРИПТ 📜", url=url)]
    ])

def kb_force_sub(script_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data=f"check_sub:{script_id}")]
    ])

def kb_broadcast_confirm():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить всем", callback_data="broadcast_send")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")]
    ])

# --- ЛОГИКА ---

router = Router()

@router.message(CommandStart())
async def start_handler(msg: Message, command: CommandObject, state: FSMContext, bot: Bot):
    args = command.args
    user_id = msg.from_user.id
    
    # Сохраняем пользователя в базу для рассылки
    await add_user_to_db(user_id)

    # 1. Если это Админ -> Сразу панель (без паролей)
    if is_admin(user_id) and not args:
        await state.clear()
        await msg.answer(
            f"👋 <b>Привет, Админ!</b>\nТы авторизован автоматически.",
            reply_markup=kb_admin_main(),
            parse_mode=ParseMode.HTML
        )
        return
    
    # 2. Получение скрипта
    if args:
        script_id = args
        script_data = await get_script_from_db(script_id)
        
        if not script_data:
            return await msg.answer("❌ Скрипт не найден или был удален.")
            
        is_sub = await check_subscription(bot, user_id)
        if not is_sub:
            return await msg.answer(
                "🔒 <b>Доступ закрыт!</b>\n\n"
                f"Чтобы получить скрипт для <b>{script_data['game']}</b>, подпишись на наш канал.",
                reply_markup=kb_force_sub(script_id),
                parse_mode=ParseMode.HTML
            )
        await send_script_to_user(msg, script_data)
        return

    await msg.answer("👋 Привет! Я выдаю скрипты с канала Ravion.\nНайди нужный пост в канале и нажми кнопку.")

@router.callback_query(F.data.startswith("check_sub:"))
async def check_sub_callback(cb: CallbackQuery, bot: Bot):
    script_id = cb.data.split(":")[1]
    is_sub = await check_subscription(bot, cb.from_user.id)
    if is_sub:
        await cb.message.delete()
        script_data = await get_script_from_db(script_id)
        if script_data: await send_script_to_user(cb.message, script_data)
        else: await cb.answer("❌ Скрипт не найден", show_alert=True)
    else:
        await cb.answer("❌ Вы все еще не подписаны!", show_alert=True)

async def send_script_to_user(msg_obj: Message, data: dict):
    code = data['code']
    game = data['game']
    header = f"🎮 <b>{game}</b>\n"
    if data['key']: header += "🔐 <b>Требуется ключ!</b>\n"
    
    file_data = code.encode('utf-8')
    input_file = BufferedInputFile(file_data, filename=f"{game}_script.lua")
    
    await msg_obj.answer_document(
        input_file,
        caption=f"{header}\n✅ <b>Скрипт готов!</b>\n\nСпасибо что используешь Ravion!",
        parse_mode=ParseMode.HTML
    )

# --- АДМИНКА: СОЗДАНИЕ ПОСТА ---

@router.message(F.text == "➕ Новый пост")
async def new_post(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    info_msg = await msg.answer("📝 <b>Создание поста</b>\n1. Имя игры\n2. Описание\n3. Код (в блоке ``` или с local/loadstring)", parse_mode=ParseMode.HTML)
    instruction_messages[msg.chat.id] = info_msg.message_id
    await state.set_state(Form.waiting_content)

@router.message(Form.waiting_content)
async def process_content(msg: Message, state: FSMContext):
    # Проверка на команды меню во время создания
    if msg.text == "👤 Профиль Админа": return await profile(msg)
    if msg.text == "➕ Новый пост": return await new_post(msg, state)
    if msg.text == "📢 Рассылка": return await start_broadcast(msg, state)

    if msg.chat.id in instruction_messages:
        try: await msg.bot.delete_message(msg.chat.id, instruction_messages[msg.chat.id])
        except: pass

    ctype = 'text'
    fid = None
    text = msg.text or msg.caption or ""
    
    if msg.photo: ctype, fid = 'photo', msg.photo[-1].file_id
    elif msg.video: ctype, fid = 'video', msg.video.file_id
    elif msg.animation: ctype, fid = 'animation', msg.animation.file_id
    
    parsed = parse_content(text)
    if not parsed['code']: return await msg.answer("⚠️ <b>Ошибка:</b> Код не найден.", parse_mode=ParseMode.HTML)

    await state.update_data(ctype=ctype, fid=fid, parsed=parsed, creator_id=msg.from_user.id)
    preview_text = build_channel_post_text(await state.get_data()) + "\n\n<i>(Это превью для админа)</i>"
    
    try:
        kwargs = {"caption": preview_text, "parse_mode": ParseMode.HTML, "reply_markup": kb_preview()}
        if ctype == 'photo': await msg.answer_photo(fid, **kwargs)
        elif ctype == 'video': await msg.answer_video(fid, **kwargs)
        elif ctype == 'animation': await msg.answer_animation(fid, **kwargs)
        else: await msg.answer(preview_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb_preview())
    except Exception as e: await msg.answer(f"❌ Ошибка превью: {e}")

@router.callback_query(F.data == "pub_now")
async def pub_now(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data: return await cb.answer("❌ Данные устарели", show_alert=True)
    code_text = "\n".join(data['parsed']['code'])
    script_id = await add_script_to_db(data['parsed']['game'], code_text, data['parsed']['key'])
    data['script_id'] = script_id
    
    # Запуск публикации
    asyncio.create_task(publish_post(cb.bot, data)) 
    
    await state.clear()
    await cb.message.delete()
    await cb.answer("✅ Пост отправлен в канал!")

@router.callback_query(F.data == "schedule")
async def schedule_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_time)
    await cb.message.delete()
    await cb.message.answer("⏰ Введите время (например: <code>1ч</code> или <code>15:30</code>):", parse_mode=ParseMode.HTML)

@router.message(Form.waiting_time)
async def schedule_finish(msg: Message, state: FSMContext):
    t = parse_time(msg.text)
    if not t: return await msg.answer("⚠️ Неверный формат.")
    data = await state.get_data()
    code_text = "\n".join(data['parsed']['code'])
    script_id = await add_script_to_db(data['parsed']['game'], code_text, data['parsed']['key'])
    data['script_id'] = script_id
    pid = f"{data['creator_id']}_{int(datetime.now().timestamp())}"
    scheduled_posts[pid] = {'data': data, 'time': t, 'creator_id': msg.from_user.id}
    await state.clear()
    await msg.answer(f"✅ Запланировано на {t.strftime('%H:%M')}", reply_markup=kb_admin_main())

# --- АДМИНКА: РАССЫЛКА (Broadcast) ---

@router.message(F.text == "📢 Рассылка")
async def start_broadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    await state.set_state(BroadcastState.waiting_message)
    await msg.answer(
        "📢 <b>Режим рассылки</b>\n\n"
        "Отправьте сообщение, которое нужно разослать всем пользователям бота.\n"
        "Поддерживается: Текст, Фото, Видео, Файлы, Голосовые, Пересылка.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")]])
    )

@router.message(BroadcastState.waiting_message)
async def process_broadcast_msg(msg: Message, state: FSMContext):
    # Сохраняем ID чата и ID сообщения для копирования
    await state.update_data(from_chat_id=msg.chat.id, message_id=msg.message_id)
    await state.set_state(BroadcastState.confirm_send)
    await msg.answer(
        "👀 <b>Предпросмотр:</b> Сообщение выше будет отправлено всем.\nПодтверждаете?",
        reply_markup=kb_broadcast_confirm(),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.delete()
    await cb.message.answer("❌ Рассылка отменена", reply_markup=kb_admin_main())

@router.callback_query(F.data == "broadcast_send")
async def broadcast_send(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    from_chat = data['from_chat_id']
    msg_id = data['message_id']
    
    users = await get_all_users()
    await cb.message.edit_text(f"🚀 <b>Начинаю рассылку на {len(users)} пользователей...</b>", parse_mode=ParseMode.HTML)
    
    success = 0
    blocked = 0
    
    for uid in users:
        try:
            await cb.bot.copy_message(chat_id=uid, from_chat_id=from_chat, message_id=msg_id)
            success += 1
            await asyncio.sleep(0.05) # Анти-флуд
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await cb.bot.copy_message(chat_id=uid, from_chat_id=from_chat, message_id=msg_id)
                success += 1
            except: blocked += 1
        except Exception:
            blocked += 1
            
    await state.clear()
    await cb.message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📩 Доставлено: <b>{success}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_admin_main()
    )

# --- АДМИНКА: ПРОФИЛЬ И СТАТИСТИКА ---

@router.message(F.text == "👤 Профиль Админа")
async def profile(msg: Message):
    if not is_admin(msg.from_user.id): return
    scripts_c, views_c, users_c = await get_db_stats()
    queue_len = len(scheduled_posts)
    
    text = (
        f"👨‍💻 <b>Панель Администратора Ravion</b>\n\n"
        f"👥 Пользователей в базе: <b>{users_c}</b>\n"
        f"💾 Скриптов в базе: <b>{scripts_c}</b>\n"
        f"👁 Всего выдано скриптов: <b>{views_c}</b>\n"
        f"⏳ Постов в очереди: <b>{queue_len}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📂 Очередь публикаций", callback_data="view_queue")]])
    await msg.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

@router.callback_query(F.data == "view_queue")
async def view_queue(cb: CallbackQuery):
    if not scheduled_posts: return await cb.answer("📭 Очередь пуста", show_alert=True)
    for pid, post in sorted(scheduled_posts.items(), key=lambda x: x[1]['time']):
        await cb.message.answer(f"⏰ {post['time'].strftime('%d.%m %H:%M')} | {post['data']['parsed']['game']}", reply_markup=kb_queue_control(pid))
    await cb.answer()

@router.callback_query(F.data == "cancel")
async def cancel_action(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.delete()

@router.callback_query(F.data.startswith("force_") | F.data.startswith("del_"))
async def queue_action(cb: CallbackQuery):
    action, pid = cb.data.split("_", 1)
    if pid in scheduled_posts:
        if action == "del":
            del scheduled_posts[pid]
            await cb.answer("🗑 Удалено")
        elif action == "force":
            scheduled_posts[pid]['time'] = datetime.now() - timedelta(seconds=1)
            await cb.answer("🚀 Запуск...")
        await cb.message.delete()
    else: await cb.answer("Ошибка", show_alert=True)

# --- ПУБЛИКАЦИЯ В КАНАЛ ---

async def publish_post(bot: Bot, data: Dict):
    text = build_channel_post_text(data)
    ctype, fid = data['ctype'], data['fid']
    script_id = data['script_id']
    kb = kb_get_script(script_id)
    
    try:
        # ПУБЛИКАЦИЯ В КАНАЛ (ИСПОЛЬЗУЕМ INT ID)
        if ctype == 'photo': await bot.send_photo(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'video': await bot.send_video(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'animation': await bot.send_animation(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else: await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb)
        
        # Уведомление админа
        await bot.send_message(data['creator_id'], f"✅ <b>Пост успешно опубликован!</b>", parse_mode=ParseMode.HTML)
    except Exception as e: 
        logger.error(f"Ошибка публикации: {e}")
        await bot.send_message(data['creator_id'], f"❌ <b>Ошибка публикации:</b>\n{e}", parse_mode=ParseMode.HTML)

async def scheduler(bot: Bot):
    while True:
        now = datetime.now()
        to_pub = []
        for pid in list(scheduled_posts.keys()):
            if now >= scheduled_posts[pid]['time']:
                to_pub.append(scheduled_posts[pid]['data'])
                del scheduled_posts[pid]
        if to_pub: await asyncio.gather(*[publish_post(bot, d) for d in to_pub])
        await asyncio.sleep(5)

async def main():
    await init_db()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(dp.start_polling(bot), scheduler(bot))

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
