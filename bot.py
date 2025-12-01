import logging
import asyncio
import re
import json
import aiosqlite
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

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
# Ссылка на твой скрипт-лоадер или канал
WATERMARK_URL = "https://t.me/RavionScripts" 
ADMINS = {7637946765, 6510703948}
DB_NAME = "posts.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
instruction_messages: Dict[int, int] = {}

class Form(StatesGroup):
    waiting_content = State()
    waiting_time = State()

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                pid TEXT PRIMARY KEY,
                creator_id INTEGER,
                creator_name TEXT,
                parsed_json TEXT,
                time_timestamp REAL
            )
        """)
        await db.commit()

async def add_post_to_db(pid: str, data: Dict, publish_time: datetime):
    async with aiosqlite.connect(DB_NAME) as db:
        # Сериализуем данные (data) в JSON строку
        json_data = json.dumps(data, ensure_ascii=False)
        await db.execute(
            "INSERT INTO posts (pid, creator_id, creator_name, parsed_json, time_timestamp) VALUES (?, ?, ?, ?, ?)",
            (pid, data['creator_id'], data['creator_name'], json_data, publish_time.timestamp())
        )
        await db.commit()

async def get_all_posts():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT pid, creator_id, creator_name, parsed_json, time_timestamp FROM posts") as cursor:
            rows = await cursor.fetchall()
            posts = {}
            for row in rows:
                posts[row[0]] = {
                    'creator_id': row[1],
                    'creator_name': row[2],
                    'data': json.loads(row[3]),
                    'time': datetime.fromtimestamp(row[4])
                }
            return posts

async def delete_post_from_db(pid: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM posts WHERE pid = ?", (pid,))
        await db.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def parse_content(raw_text: str) -> Dict[str, Any]:
    lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
    res = {'game': '🎮 Game', 'desc': '', 'key': False, 'code': []}
    
    if not lines: return res
    res['game'] = lines[0]
    
    code_found = False
    desc_lines = []
    
    # Регулярка для поиска loadstring(game:HttpGet(...))
    # Поддерживает любые кавычки и пробелы
    loadstring_pattern = re.compile(r'loadstring\s*\(\s*game:HttpGet\s*\(\s*[\'"](.*?)[\'"]\s*\)\s*\)\s*\(\s*\)', re.IGNORECASE)

    for line in lines[1:]:
        low = line.lower()
        if '#key' in low or 'key+' in low: res['key'] = True; continue
        if '#nokey' in low or 'key-' in low or 'no key' in low: res['key'] = False; continue
            
        # Определение начала кода
        is_code = any(x in low for x in ['loadstring', 'game:', 'function', 'local ', 'getgenv', '```'])
        
        if not code_found and is_code:
            code_found = True
            clean = line.replace('```lua', '').replace('```', '').strip()
            
            # Внедрение водяного знака
            if 'game:httpget' in clean.lower():
                # Если нашли loadstring, заменяем ссылку внутри на нашу
                if loadstring_pattern.search(clean):
                    clean = loadstring_pattern.sub(f'loadstring(game:HttpGet("{WATERMARK_URL}"))()', clean)
                elif WATERMARK_URL not in clean:
                     # Если паттерн сложный, просто добавляем комментарий
                     clean = f"-- Source: {WATERMARK_URL}\n" + clean
            
            res['code'].append(clean)
        elif code_found:
            res['code'].append(line.replace('```', ''))
        else:
            if not line.startswith('#'): desc_lines.append(line)
    
    res['desc'] = '\n'.join(desc_lines)
    return res

def build_post_text(data: Dict) -> str:
    game = html_escape(data['parsed']['game']).upper()
    desc = html_escape(data['parsed']['desc'])
    
    text = f"<b>━━━━━━━━━━━━━━━━━━━</b>\n🎮 <b>{game}</b>\n<b>━━━━━━━━━━━━━━━━━━━</b>\n\n"
    
    if desc: 
        quoted_desc = "\n".join(f"💬 {line}" for line in desc.split('\n'))
        text += f"<blockquote>{quoted_desc}</blockquote>\n\n"
        
    text += "🔐 <b>Требуется ключ</b>\n\n" if data['parsed']['key'] else "🔓 <b>Ключ не нужен</b>\n\n"
    
    if data['parsed']['code']:
        code = "\n".join(data['parsed']['code'])
        text += f"⚡ <b>СКРИПТ:</b>\n<pre><code class=\"language-lua\">{html_escape(code)}</code></pre>\n\n"
        
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

def kb_queue_control(pid: str, is_owner: bool, owner_name: str):
    buttons = []
    if is_owner:
        buttons.append([InlineKeyboardButton(text="🚀 Выложить сейчас", callback_data=f"force_{pid}")])
        buttons.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{pid}")])
    else:
        buttons.append([InlineKeyboardButton(text=f"👤 Автор: {owner_name}", callback_data="ignore")])
        buttons.append([InlineKeyboardButton(text="🔒 Только чтение", callback_data="ignore")])
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
    
    example = (
        "Blox Fruits\n"
        "Крутой скрипт на автофарм\n"
        "#key\n"
        "loadstring(game:HttpGet('...'))()"
    )
    
    info_msg = await msg.answer(
        "📝 <b>Создание нового поста</b>\n\n"
        "Отправь фото/видео с описанием или просто текст.\n"
        "Вот пример формата (нажми чтобы скопировать):\n\n"
        f"<code>{example}</code>",
        parse_mode=ParseMode.HTML
    )
    instruction_messages[msg.chat.id] = info_msg.message_id
    await state.set_state(Form.waiting_content)

@router.message(Form.waiting_content)
async def process_content(msg: Message, state: FSMContext):
    if msg.text == "👤 Профиль":
        await state.clear()
        return await profile(msg)
    if msg.text == "➕ Новый пост":
        return await new_post(msg, state)

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
        return await msg.answer("⚠️ Пустое сообщение. Попробуй снова.")
        
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
        await msg.answer(f"❌ Ошибка: {e}")

@router.callback_query(F.data == "cancel")
async def cancel_post(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.delete()
    await cb.answer("❌ Создание поста отменено")

@router.callback_query(F.data == "ignore")
async def ignore_click(cb: CallbackQuery):
    await cb.answer("🔒 Нет прав", show_alert=True)

@router.callback_query(F.data == "pub_now")
async def pub_now(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data:
        await cb.message.delete()
        return await cb.answer("❌ Данные устарели", show_alert=True)

    asyncio.create_task(publish_post(cb.bot, data)) 
    await state.clear()
    await cb.message.delete()
    await cb.answer("✅ Отправлено на публикацию...")

@router.callback_query(F.data == "schedule")
async def schedule_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_time)
    await cb.message.delete()
    await cb.message.answer(
        "⏰ <b>Когда опубликовать?</b>\n\n"
        "Примеры:\n"
        "• <code>30м</code> (через 30 мин)\n"
        "• <code>1ч</code> (через час)\n"
        "• <code>18:00</code> (сегодня/завтра)",
        parse_mode=ParseMode.HTML
    )

@router.message(Form.waiting_time)
async def schedule_finish(msg: Message, state: FSMContext):
    if msg.text == "👤 Профиль":
        await state.clear()
        return await profile(msg)
    if msg.text == "➕ Новый пост":
        return await new_post(msg, state)

    t = parse_time(msg.text)
    if not t: return await msg.answer("⚠️ Не понял время.")
    
    data = await state.get_data()
    if not data:
        await state.clear()
        return await msg.answer("❌ Ошибка данных.")

    pid = f"{data['creator_id']}_{int(datetime.now().timestamp())}"
    
    # СОХРАНЯЕМ В БД
    await add_post_to_db(pid, data, t)
    
    await state.clear()
    await msg.answer(
        f"✅ <b>Пост запланирован!</b>\n⏰ {t.strftime('%d.%m %H:%M')}", 
        parse_mode=ParseMode.HTML, reply_markup=kb_main()
    )

@router.message(F.text == "👤 Профиль")
async def profile(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    uid = msg.from_user.id
    
    # Читаем из БД для статистики
    all_posts = await get_all_posts()
    my_posts = sum(1 for p in all_posts.values() if p['creator_id'] == uid)
    total = len(all_posts)
    
    text = (
        f"👨‍💻 <b>Профиль Администратора</b>\n"
        f"👤 Имя: {msg.from_user.first_name}\n"
        f"📦 Твоих постов: <b>{my_posts}</b>\n"
        f"🌐 Всего в очереди: <b>{total}</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Открыть очередь", callback_data="view_queue")]
    ])
    await msg.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

@router.callback_query(F.data == "view_queue")
async def view_queue(cb: CallbackQuery):
    all_posts = await get_all_posts()
    
    if not all_posts:
        return await cb.answer("📭 Очередь пуста", show_alert=True)
    
    user_id = cb.from_user.id
    sorted_posts = sorted(all_posts.items(), key=lambda x: x[1]['time'])
    
    await cb.message.answer("<b>📅 ОЧЕРЕДЬ ПУБЛИКАЦИЙ:</b>", parse_mode=ParseMode.HTML)
    
    for pid, post in sorted_posts:
        game = post['data']['parsed']['game']
        t_str = post['time'].strftime('%d.%m %H:%M')
        owner = post['creator_name']
        is_owner = (post['creator_id'] == user_id)
        
        status_icon = "🟢" if is_owner else "🔴"
        
        await cb.message.answer(
            f"{status_icon} <b>{game}</b>\n"
            f"⏰ {t_str}\n"
            f"👤 Админ: {owner}",
            reply_markup=kb_queue_control(pid, is_owner, owner),
            parse_mode=ParseMode.HTML
        )
    await cb.answer()

@router.callback_query(F.data.startswith("force_") | F.data.startswith("del_"))
async def queue_action(cb: CallbackQuery):
    action, pid = cb.data.split("_", 1) 
    
    all_posts = await get_all_posts()
    post = all_posts.get(pid)
    
    if not post: 
        await cb.message.delete()
        return await cb.answer("❌ Пост уже не существует", show_alert=True)
        
    if post['creator_id'] != cb.from_user.id:
        return await cb.answer("⛔ Это не твой пост!", show_alert=True)
        
    if action == "del":
        await delete_post_from_db(pid)
        await cb.message.delete()
        await cb.answer("🗑 Пост удален")
    elif action == "force":
        # Удаляем из БД и публикуем
        await delete_post_from_db(pid)
        asyncio.create_task(publish_post(cb.bot, post['data']))
        await cb.message.delete()
        await cb.answer("🚀 Отправляю в публикацию...")

async def publish_post(bot: Bot, data: Dict):
    text = build_post_text(data)
    ctype, fid = data['ctype'], data['fid']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Скрипт в канале", url=WATERMARK_URL)]])
    
    try:
        if ctype == 'photo': await bot.send_photo(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'video': await bot.send_video(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'animation': await bot.send_animation(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'document': await bot.send_document(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else: await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb)
        
        await bot.send_message(data['creator_id'], f"✅ Твой пост <b>{data['parsed']['game']}</b> опубликован!", parse_mode=ParseMode.HTML)
    
    except TelegramForbiddenError:
        logger.error(f"Бот заблокирован пользователем {data['creator_id']}.")
    except TelegramBadRequest as e:
        logger.error(f"Ошибка API при публикации: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка: {e}")

async def scheduler(bot: Bot):
    while True:
        try:
            now = datetime.now()
            all_posts = await get_all_posts() # Получаем актуальное состояние из БД
            
            for pid, post in all_posts.items():
                if now >= post['time']:
                    # Сначала удаляем из БД, чтобы не отправить дважды при ошибке
                    await delete_post_from_db(pid)
                    # Публикуем
                    asyncio.create_task(publish_post(bot, post['data']))
            
            await asyncio.sleep(10) # Проверка каждые 10 секунд
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {e}")
            await asyncio.sleep(10)

async def main():
    await init_db() # Инициализация БД при запуске
    
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    
    await asyncio.gather(
        dp.start_polling(bot),
        scheduler(bot)
    )

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
