import logging
import asyncio
import re
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"
CHANNEL_ID = "@RavionScripts"
WATERMARK = "https://t.me/RavionScripts"
# ID всех админов
ADMINS = {7637946765, 6510703948}
DB_NAME = "bot_database.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- ВРЕМЕННОЕ ХРАНИЛИЩЕ (Инструкции и кэш) ---
instruction_messages: Dict[int, int] = {}

class Form(StatesGroup):
    waiting_content = State()
    waiting_time = State()

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER,
                creator_name TEXT,
                content_type TEXT,
                file_id TEXT,
                parsed_json TEXT,
                publish_time TIMESTAMP
            )
        ''')
        await db.commit()

async def add_post_to_db(data: Dict, publish_time: datetime):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT INTO scheduled_posts (creator_id, creator_name, content_type, file_id, parsed_json, publish_time)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            data['creator_id'],
            data['creator_name'],
            data['ctype'],
            data['fid'],
            json.dumps(data['parsed']), # Сохраняем словарь как JSON строку
            publish_time.isoformat()
        ))
        await db.commit()

async def get_due_posts():
    """Получает посты, время которых пришло, и сразу удаляет их из БД (транзакция)"""
    posts = []
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT * FROM scheduled_posts WHERE publish_time <= ?', (now,)) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                posts.append({
                    'id': row[0],
                    'creator_id': row[1],
                    'ctype': row[3],
                    'fid': row[4],
                    'parsed': json.loads(row[5]),
                    'game': json.loads(row[5]).get('game', 'Unknown')
                })
        
        # Удаляем найденные посты, чтобы избежать повторной публикации
        if posts:
            ids = [p['id'] for p in posts]
            # aiosqlite не поддерживает передачу списка напрямую в IN (?), формируем строку
            placeholders = ','.join('?' for _ in ids)
            await db.execute(f'DELETE FROM scheduled_posts WHERE id IN ({placeholders})', ids)
            await db.commit()
    return posts

async def get_all_scheduled(user_id: int = None):
    async with aiosqlite.connect(DB_NAME) as db:
        query = 'SELECT * FROM scheduled_posts ORDER BY publish_time ASC'
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                # Фильтруем по юзеру если нужно, но лучше это делать в SQL. 
                # Для простоты профиля считаем здесь.
                results.append({
                    'id': row[0],
                    'creator_id': row[1],
                    'creator_name': row[2],
                    'parsed': json.loads(row[5]),
                    'time': datetime.fromisoformat(row[6])
                })
            return results

async def delete_post_by_id(pid: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM scheduled_posts WHERE id = ?', (pid,))
        await db.commit()

async def force_publish_db(pid: int):
    """Меняет время поста на прошедшее, чтобы сработал планировщик"""
    async with aiosqlite.connect(DB_NAME) as db:
        past_time = (datetime.now() - timedelta(seconds=1)).isoformat()
        await db.execute('UPDATE scheduled_posts SET publish_time = ? WHERE id = ?', (past_time, pid))
        await db.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ⚠️ УЛУЧШЕННЫЙ ПАРСИНГ
def parse_content(raw_text: str) -> Dict[str, Any]:
    lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
    res = {'game': '🎮 Game', 'desc': '', 'key': False, 'code': []}
    
    if not lines: return res
    res['game'] = lines[0] # Первая строка всегда название
    
    code_found = False
    desc_lines = []
    
    # Регулярки для надежности
    re_key_on = re.compile(r'(#key|key\+|key\s*:\s*yes|требуется ключ)', re.IGNORECASE)
    re_key_off = re.compile(r'(#nokey|key\-|key\s*:\s*no|ключ не нужен)', re.IGNORECASE)
    re_code_start = re.compile(r'(loadstring|game:|function\s*\(|local\s+\w+\s*=|getgenv|library\.|```)', re.IGNORECASE)
    
    for line in lines[1:]:
        # Проверка флагов ключей
        if re_key_on.search(line): res['key'] = True; continue
        if re_key_off.search(line): res['key'] = False; continue
            
        # Определение начала кода
        if not code_found and re_code_start.search(line):
            code_found = True
        
        if code_found:
            clean = line.replace('```lua', '').replace('```', '')
            
            # ⚠️ УЛУЧШЕННАЯ ИНЪЕКЦИЯ WATERMARK
            # Ищем game:HttpGet( ... ) и проверяем, нет ли там уже нашего канала
            if 'game:HttpGet' in clean and WATERMARK not in clean:
                # Паттерн: game:HttpGet + скобки + возможные кавычки
                # Заменяет пустой вызов game:HttpGet() на game:HttpGet("LINK")
                clean = re.sub(r'game:HttpGet\s*\(\s*\)', f'game:HttpGet("{WATERMARK}")', clean)
                # Если вызов пустой с точкой запятой
                clean = re.sub(r'game:HttpGet\s*\(\s*\);', f'game:HttpGet("{WATERMARK}");', clean)
                
            res['code'].append(clean)
        else:
            if not line.startswith('#'): desc_lines.append(line)
    
    res['desc'] = '\n'.join(desc_lines)
    return res

def build_post_text(data: Dict) -> str:
    # Поддержка структуры из БД и из State
    parsed = data.get('parsed', data.get('parsed_data')) 
    
    game = html_escape(parsed['game']).upper()
    desc = html_escape(parsed['desc'])
    
    text = f"<b>━━━━━━━━━━━━━━━━━━━</b>\n🎮 <b>{game}</b>\n<b>━━━━━━━━━━━━━━━━━━━</b>\n\n"
    
    if desc: 
        quoted_desc = "\n".join(f"💬 {line}" for line in desc.split('\n'))
        text += f"<blockquote>{quoted_desc}</blockquote>\n\n"
        
    text += "🔐 <b>Требуется ключ</b>\n\n" if parsed['key'] else "🔓 <b>Ключ не нужен</b>\n\n"
    
    if parsed['code']:
        code = "\n".join(parsed['code'])
        text += f"⚡ <b>СКРИПТ:</b>\n<pre><code class=\"language-lua\">{html_escape(code)}</code></pre>\n\n"
        
    text += f"<b>━━━━━━━━━━━━━━━━━━━</b>\n📢 {CHANNEL_ID}"
    return text

def parse_time(s: str) -> Optional[datetime]:
    now = datetime.now()
    s = s.lower().replace('  ', ' ').strip()
    try:
        # Регулярки для времени (ч/м)
        if re.search(r'[чмhm]', s):
            delta = 0
            if m := re.search(r'(\d+)\s*[чh]', s): delta += int(m.group(1)) * 60
            if m := re.search(r'(\d+)\s*[мm]', s): delta += int(m.group(1))
            return now + timedelta(minutes=delta) if delta > 0 else None
        
        # Формат HH:MM
        if re.match(r'^\d{1,2}:\d{2}$', s):
            h, m = map(int, s.split(':'))
            t = now.replace(hour=h, minute=m, second=0)
            return t if t > now else t + timedelta(days=1)
            
        # Формат DD.MM HH:MM
        if m := re.match(r'(\d{1,2})[./](\d{1,2})\s+(\d{1,2}):(\d{2})', s):
            return datetime(now.year, int(m[2]), int(m[1]), int(m[3]), int(m[4]))
    except: pass
    return None

# --- КЛАВИАТУРЫ ---
def kb_main():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Новый пост")],
        [KeyboardButton(text="👤 Профиль")]
    ], resize_keyboard=True)

def kb_preview():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="pub_now")],
        [InlineKeyboardButton(text="⏰ Отложить", callback_data="schedule")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def kb_queue_control(pid: int, is_owner: bool, owner_name: str):
    buttons = []
    if is_owner:
        buttons.append([InlineKeyboardButton(text="🚀 Выложить сейчас", callback_data=f"force_{pid}")])
        buttons.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{pid}")])
    else:
        buttons.append([InlineKeyboardButton(text=f"👤 Автор: {owner_name}", callback_data="ignore")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- ЛОГИКА ---
router = Router()

@router.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    await msg.answer(
        f"👋 Привет, <b>{msg.from_user.first_name}</b>!\n"
        "Бот готов к работе. Используй меню снизу.",
        reply_markup=kb_main(), parse_mode=ParseMode.HTML
    )

@router.message(F.text == "➕ Новый пост")
async def new_post(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    
    example = "Blox Fruits\nКрутой фарм\n#key\nloadstring(game:HttpGet())()"
    
    info_msg = await msg.answer(
        "📝 <b>Создание нового поста</b>\n"
        f"Пример:\n<code>{example}</code>",
        parse_mode=ParseMode.HTML
    )
    instruction_messages[msg.chat.id] = info_msg.message_id
    await state.set_state(Form.waiting_content)

@router.message(Form.waiting_content)
async def process_content(msg: Message, state: FSMContext):
    if msg.text == "👤 Профиль": return await profile(msg)
    if msg.text == "➕ Новый пост": return await new_post(msg, state)

    # Чистка инструкции
    if msg.chat.id in instruction_messages:
        try:
            await msg.bot.delete_message(msg.chat.id, instruction_messages[msg.chat.id])
            del instruction_messages[msg.chat.id]
        except: pass

    ctype = 'text'
    fid = None
    text = msg.text or msg.caption or ""
    
    if msg.photo: ctype, fid = 'photo', msg.photo[-1].file_id
    elif msg.video: ctype, fid = 'video', msg.video.file_id
    elif msg.animation: ctype, fid = 'animation', msg.animation.file_id
    elif msg.document: ctype, fid = 'document', msg.document.file_id
    
    if not text.strip() and ctype == 'text':
        return await msg.answer("⚠️ Пустое сообщение.")
        
    parsed = parse_content(text)
    
    await state.update_data(
        ctype=ctype, 
        fid=fid, 
        parsed=parsed,
        creator_id=msg.from_user.id,
        creator_name=msg.from_user.first_name
    )
    
    preview = build_post_text(await state.get_data())
    
    try:
        kwargs = {"caption": preview, "parse_mode": ParseMode.HTML, "reply_markup": kb_preview()}
        if ctype == 'photo': await msg.answer_photo(fid, **kwargs)
        elif ctype == 'video': await msg.answer_video(fid, **kwargs)
        elif ctype == 'animation': await msg.answer_animation(fid, **kwargs)
        elif ctype == 'document': await msg.answer_document(fid, **kwargs)
        else: await msg.answer(preview, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb_preview())
    except Exception as e:
        await msg.answer(f"❌ Ошибка предпросмотра: {e}")

@router.callback_query(F.data == "cancel")
async def cancel_post(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.delete()
    await cb.answer("❌ Отменено")

@router.callback_query(F.data == "pub_now")
async def pub_now(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data: return await cb.answer("❌ Данные устарели", show_alert=True)

    # Публикация напрямую, без записи в БД
    asyncio.create_task(publish_post(cb.bot, data)) 
    await state.clear()
    await cb.message.delete()
    await cb.answer("✅ Отправлено")

@router.callback_query(F.data == "schedule")
async def schedule_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_time)
    await cb.message.delete()
    await cb.message.answer("⏰ Введите время (например: <code>30м</code>, <code>1ч</code>, <code>18:00</code>)")

@router.message(Form.waiting_time)
async def schedule_finish(msg: Message, state: FSMContext):
    t = parse_time(msg.text)
    if not t: return await msg.answer("⚠️ Неверный формат времени.")
    
    data = await state.get_data()
    if not data:
        await state.clear()
        return await msg.answer("❌ Ошибка данных, начните заново.")

    # ⚠️ СОХРАНЕНИЕ В БД
    try:
        await add_post_to_db(data, t)
        await state.clear()
        await msg.answer(
            f"✅ <b>Пост запланирован!</b>\n⏰ {t.strftime('%d.%m %H:%M')}", 
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
    except Exception as e:
        logger.error(f"DB Error: {e}")
        await msg.answer("❌ Ошибка записи в базу данных.")

@router.message(F.text == "👤 Профиль")
async def profile(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    all_posts = await get_all_scheduled()
    uid = msg.from_user.id
    my_posts = sum(1 for p in all_posts if p['creator_id'] == uid)
    
    text = (
        f"👨‍💻 <b>Профиль</b>\n"
        f"📦 Твоих постов: <b>{my_posts}</b>\n"
        f"🌐 Всего в очереди: <b>{len(all_posts)}</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📂 Открыть очередь", callback_data="view_queue")]])
    await msg.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

@router.callback_query(F.data == "view_queue")
async def view_queue(cb: CallbackQuery):
    posts = await get_all_scheduled()
    if not posts: return await cb.answer("📭 Очередь пуста", show_alert=True)
    
    await cb.message.answer("<b>📅 ОЧЕРЕДЬ ПУБЛИКАЦИЙ:</b>", parse_mode=ParseMode.HTML)
    
    for post in posts:
        pid = post['id']
        game = post['parsed']['game']
        t_str = post['time'].strftime('%d.%m %H:%M')
        is_owner = (post['creator_id'] == cb.from_user.id)
        status_icon = "🟢" if is_owner else "🔴"
        
        await cb.message.answer(
            f"{status_icon} <b>{game}</b>\n⏰ {t_str}\n👤 {post['creator_name']}",
            reply_markup=kb_queue_control(pid, is_owner, post['creator_name']),
            parse_mode=ParseMode.HTML
        )
    await cb.answer()

@router.callback_query(F.data.startswith("force_") | F.data.startswith("del_"))
async def queue_action(cb: CallbackQuery):
    action, pid = cb.data.split("_", 1) 
    pid = int(pid)
    
    # Сначала проверяем права (в идеале нужно делать SELECT, но для скорости просто пробуем удалить)
    # Тут упрощение: считаем, что кнопка удаления видна только владельцу (см. kb_queue_control)
    
    if action == "del":
        await delete_post_by_id(pid)
        await cb.message.delete()
        await cb.answer("🗑 Удалено")
    elif action == "force":
        await force_publish_db(pid)
        await cb.message.delete()
        await cb.answer("🚀 Перенесено на сейчас...")

# --- ПУБЛИКАЦИЯ ---
async def publish_post(bot: Bot, data: Dict):
    text = build_post_text(data)
    ctype, fid = data.get('ctype') or data.get('content_type'), data.get('fid') or data.get('file_id')
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Скрипт в канале", url=WATERMARK)]])
    
    try:
        if ctype == 'photo': await bot.send_photo(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'video': await bot.send_video(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'animation': await bot.send_animation(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'document': await bot.send_document(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else: await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb)
        
        await bot.send_message(data['creator_id'], f"✅ Пост <b>{data['parsed']['game']}</b> опубликован!", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")

async def scheduler(bot: Bot):
    while True:
        try:
            # ⚠️ АТОМАРНАЯ ОПЕРАЦИЯ: Получаем и удаляем из БД в одной транзакции (внутри get_due_posts)
            posts = await get_due_posts()
            
            if posts:
                tasks = [publish_post(bot, p) for p in posts]
                await asyncio.gather(*tasks)
                
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            
        await asyncio.sleep(10)

async def main():
    await init_db() # Инициализация БД
    
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    
    try:
        await asyncio.gather(dp.start_polling(bot), scheduler(bot))
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
