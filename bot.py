import logging
import asyncio
import re
import uuid
import aiosqlite
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile
)
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"
CHANNEL_ID = "@RavionScripts" # ID канала (или @username)
CHANNEL_URL = "https://t.me/RavionScripts" # Ссылка для кнопки подписки
BOT_USERNAME = "RavionAdministrator_bot" # УКАЖИ ЮЗЕРНЕЙМ СВОЕГО БОТА (без @) ДЛЯ ГЕНЕРАЦИИ ССЫЛОК
WATERMARK = "https://t.me/RavionScripts"

# ID админов (Int)
ADMINS = {7637946765, 6510703948} 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- БАЗА ДАННЫХ (SQLite) ---
DB_NAME = "scripts.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scripts (
                id TEXT PRIMARY KEY,
                game_name TEXT,
                code TEXT,
                views INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def add_script_to_db(game_name: str, code: str) -> str:
    # Генерируем короткий уникальный ID (8 символов)
    script_id = uuid.uuid4().hex[:8]
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO scripts (id, game_name, code) VALUES (?, ?, ?)", 
                         (script_id, game_name, code))
        await db.commit()
    return script_id

async def get_script_from_db(script_id: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT game_name, code, views FROM scripts WHERE id = ?", (script_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                # Увеличиваем счетчик просмотров
                await db.execute("UPDATE scripts SET views = views + 1 WHERE id = ?", (script_id,))
                await db.commit()
                return {'game': row[0], 'code': row[1], 'views': row[2]}
    return None

async def get_db_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*), SUM(views) FROM scripts") as cursor:
            row = await cursor.fetchone()
            return {'count': row[0] or 0, 'total_views': row[1] or 0}

# --- ХРАНИЛИЩЕ (В ПАМЯТИ) ---
scheduled_posts: Dict[str, Dict[str, Any]] = {}

class Form(StatesGroup):
    waiting_content = State()
    waiting_time = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

async def check_subscription(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        # Статусы, которые считаются "подписанными"
        return member.status in [
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.RESTRICTED 
        ]
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        return False

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

def build_post_text(data: Dict, for_channel: bool = False) -> str:
    game = html_escape(data['parsed']['game']).upper()
    desc = html_escape(data['parsed']['desc'])
    
    text = f"<b>━━━━━━━━━━━━━━━━━━━</b>\n🎮 <b>{game}</b>\n<b>━━━━━━━━━━━━━━━━━━━</b>\n\n"
    
    if desc: 
        quoted_desc = "\n".join(f"💬 {line}" for line in desc.split('\n'))
        text += f"<blockquote>{quoted_desc}</blockquote>\n\n"
        
    text += "🔐 <b>Требуется ключ</b>\n\n" if data['parsed']['key'] else "🔓 <b>Ключ не нужен</b>\n\n"
    
    # В канал код не пишем, пишем только в предпросмотре для админа
    if not for_channel and data['parsed']['code']:
        text += f"⚡ <b>СКРИПТ (ВИДЕН ТОЛЬКО АДМИНУ):</b>\n<pre><code class=\"language-lua\">...код скрыт...</code></pre>\n\n"
    
    if for_channel:
         text += "⬇️ <b>Нажми на кнопку ниже, чтобы получить скрипт!</b>\n\n"

    text += f"<b>━━━━━━━━━━━━━━━━━━━</b>\n📢 {CHANNEL_ID}"
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

def kb_main_admin():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Новый пост")],
        [KeyboardButton(text="👤 Профиль Админа")]
    ], resize_keyboard=True)

def kb_preview():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="pub_now")],
        [InlineKeyboardButton(text="⏰ Отложить", callback_data="schedule")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def kb_sub_check(script_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="🔄 Я подписался", callback_data=f"checksub_{script_id}")]
    ])

# --- ЛОГИКА ---

router = Router()

@router.message(CommandStart())
async def cmd_start(msg: Message, command: CommandObject, state: FSMContext):
    user_id = msg.from_user.id
    args = command.args

    # 1. Логика Deep Linking (получение скрипта)
    if args:
        # Аргумент должен быть ID скрипта
        script_id = args
        
        # Проверка подписки
        is_sub = await check_subscription(msg.bot, user_id)
        if not is_sub:
            await msg.answer(
                "⛔ <b>Доступ закрыт!</b>\n\n"
                "Чтобы получить скрипт, вы должны быть подписаны на наш канал.",
                reply_markup=kb_sub_check(script_id),
                parse_mode=ParseMode.HTML
            )
            return

        # Если подписан, выдаем скрипт
        script_data = await get_script_from_db(script_id)
        if script_data:
            code_text = "\n".join([script_data['code']]) # Если там массив строк, объединить
            await msg.answer(
                f"✅ <b>Скрипт для {script_data['game']}</b>\n"
                f"👀 Просмотров: {script_data['views']}\n\n"
                f"<pre><code class=\"language-lua\">{html_escape(code_text)}</code></pre>",
                parse_mode=ParseMode.HTML
            )
        else:
            await msg.answer("❌ Скрипт не найден. Возможно, он был удален.")
        return

    # 2. Логика для Админов (без аргументов)
    if is_admin(user_id):
        await state.clear()
        await msg.answer(
            f"👋 Привет, Админ <b>{msg.from_user.first_name}</b>!\n"
            "Панель управления активирована.",
            reply_markup=kb_main_admin(), parse_mode=ParseMode.HTML
        )
        return

    # 3. Логика для Обычных пользователей (без аргументов)
    await msg.answer(
        f"👋 Привет, <b>{msg.from_user.first_name}</b>!\n"
        f"Я бот для выдачи скриптов с канала {CHANNEL_ID}.\n"
        "Следи за новостями, нажимай на кнопки под постами и получай скрипты здесь!",
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("checksub_"))
async def callback_check_sub(cb: CallbackQuery):
    script_id = cb.data.split("_")[1]
    is_sub = await check_subscription(cb.bot, cb.from_user.id)
    
    if is_sub:
        await cb.message.delete()
        script_data = await get_script_from_db(script_id)
        if script_data:
            code_text = script_data['code']
            await cb.message.answer(
                f"✅ <b>Спасибо за подписку!</b>\n\n"
                f"🎮 Игра: <b>{script_data['game']}</b>\n"
                f"👇 Твой скрипт:\n"
                f"<pre><code class=\"language-lua\">{html_escape(code_text)}</code></pre>",
                parse_mode=ParseMode.HTML
            )
        else:
            await cb.message.answer("❌ Скрипт не найден в базе.")
    else:
        await cb.answer("❌ Вы все еще не подписаны!", show_alert=True)

# --- АДМИНКА ---

@router.message(F.text == "➕ Новый пост")
async def new_post(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    
    example = (
        "Blox Fruits\n"
        "Крутой скрипт на автофарм\n"
        "#key\n"
        "loadstring(game:HttpGet('...'))()"
    )
    
    await msg.answer(
        "📝 <b>Создание нового поста</b>\n"
        "Отправь фото/видео с описанием.\n"
        "Скрипт будет автоматически вырезан и сохранен в БД.\n\n"
        f"Пример:\n<code>{example}</code>",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(Form.waiting_content)

@router.message(Form.waiting_content)
async def process_content(msg: Message, state: FSMContext):
    if msg.text == "👤 Профиль Админа": return await profile(msg)
    if msg.text == "➕ Новый пост": return await new_post(msg, state)

    ctype = 'text'
    fid = None
    text = msg.text or msg.caption or ""
    
    if msg.photo: ctype, fid = 'photo', msg.photo[-1].file_id
    elif msg.video: ctype, fid = 'video', msg.video.file_id
    
    if not text.strip() and ctype == 'text':
        return await msg.answer("⚠️ Пустое сообщение.")
        
    parsed = parse_content(text)
    
    if not parsed['code']:
        return await msg.answer("⚠️ Я не нашел код скрипта в сообщении! Добавь loadstring или ```lua ... ```.")

    await state.update_data(
        ctype=ctype, 
        fid=fid, 
        parsed=parsed,
        creator_id=msg.from_user.id
    )
    
    # Предпросмотр (показывает как будет в канале, но без рабочей кнопки пока)
    preview_text = build_post_text(await state.get_data(), for_channel=True)
    
    try:
        kwargs = {"caption": preview_text, "parse_mode": ParseMode.HTML, "reply_markup": kb_preview()}
        if ctype == 'photo': await msg.answer_photo(fid, **kwargs)
        elif ctype == 'video': await msg.answer_video(fid, **kwargs)
        else: await msg.answer(preview_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb_preview())
        
        # Показываем админу код отдельно, чтобы он проверил, что бот поймал
        code_view = "\n".join(parsed['code'])
        await msg.answer(f"⚙️ <b>Скрипт, который будет сохранен в БД:</b>\n<pre>{html_escape(code_view)}</pre>", parse_mode=ParseMode.HTML)
        
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")

@router.callback_query(F.data == "pub_now")
async def pub_now(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data: return await cb.answer("❌ Данные устарели", show_alert=True)

    # 1. Сохраняем скрипт в БД и получаем ID
    full_code = "\n".join(data['parsed']['code'])
    script_id = await add_script_to_db(data['parsed']['game'], full_code)
    
    # 2. Обновляем данные, добавляя ID скрипта
    data['script_id'] = script_id
    
    # 3. Публикуем
    asyncio.create_task(publish_post(cb.bot, data)) 
    
    await state.clear()
    await cb.message.delete()
    await cb.answer("✅ Отправлено!")

@router.callback_query(F.data == "schedule")
async def schedule_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_time)
    await cb.message.delete()
    await cb.message.answer("⏰ Введите время (например: 1ч, 18:00):")

@router.message(Form.waiting_time)
async def schedule_finish(msg: Message, state: FSMContext):
    t = parse_time(msg.text)
    if not t: return await msg.answer("⚠️ Неверный формат времени.")
    
    data = await state.get_data()
    
    # Сначала сохраняем в БД, чтобы получить ID
    full_code = "\n".join(data['parsed']['code'])
    script_id = await add_script_to_db(data['parsed']['game'], full_code)
    data['script_id'] = script_id

    pid = f"{data['creator_id']}_{int(datetime.now().timestamp())}"
    
    scheduled_posts[pid] = {
        'data': data,
        'time': t,
        'creator_id': data['creator_id']
    }
    
    await state.clear()
    await msg.answer(f"✅ Пост с ID скрипта <code>{script_id}</code> запланирован на {t.strftime('%H:%M')}", parse_mode=ParseMode.HTML, reply_markup=kb_main_admin())

@router.message(F.text == "👤 Профиль Админа")
async def profile(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    stats = await get_db_stats()
    
    text = (
        f"👨‍💻 <b>Админ Панель</b>\n"
        f"👤 {msg.from_user.first_name}\n\n"
        f"📊 <b>Статистика БД:</b>\n"
        f"📂 Скриптов в базе: <b>{stats['count']}</b>\n"
        f"👀 Всего выдач (просмотров): <b>{stats['total_views']}</b>\n"
        f"⏳ Запланировано постов: {len(scheduled_posts)}"
    )
    
    await msg.answer(text, parse_mode=ParseMode.HTML)

# --- ПУБЛИКАЦИЯ ---

async def publish_post(bot: Bot, data: Dict):
    text = build_post_text(data, for_channel=True)
    ctype, fid = data['ctype'], data['fid']
    script_id = data.get('script_id')
    
    # Генерируем ссылку на бот с ID скрипта
    # Формат: https://t.me/BotUsername?start=script_id
    bot_link = f"https://t.me/{BOT_USERNAME}?start={script_id}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 ПОЛУЧИТЬ СКРИПТ", url=bot_link)]
    ])
    
    try:
        if ctype == 'photo': await bot.send_photo(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'video': await bot.send_video(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else: await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb)
        
        await bot.send_message(data['creator_id'], f"✅ Пост опубликован! ID скрипта: <code>{script_id}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")

async def scheduler(bot: Bot):
    while True:
        now = datetime.now()
        posts_to_publish = []
        for pid in list(scheduled_posts.keys()):
            post = scheduled_posts[pid]
            if now >= post['time']:
                posts_to_publish.append((pid, post['data']))
                del scheduled_posts[pid]
        
        if posts_to_publish:
            tasks = [publish_post(bot, data) for pid, data in posts_to_publish]
            await asyncio.gather(*tasks, return_exceptions=True)
            
        await asyncio.sleep(5)

async def main():
    # Инициализация БД
    await init_db()
    
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(dp.start_polling(bot), scheduler(bot))

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
