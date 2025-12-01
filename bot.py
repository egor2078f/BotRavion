import logging
import re
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any
import dateparser

try:
    from aiogram import Bot, Dispatcher, F, Router
    from aiogram.types import (
        Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
        ReplyKeyboardMarkup, KeyboardButton, FSInputFile
    )
    from aiogram.filters import Command, CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.fsm.storage.memory import MemoryStorage
except ImportError:
    print("CRITICAL ERROR: Установите: pip install aiogram dateparser")
    exit(1)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"
CHANNEL = "@RavionScripts"
WATERMARK_URL = "https://t.me/RavionScripts"
ALLOWED_USERS = {7637946765, 6510703948}

user_data: Dict[int, Dict[str, Any]] = {}
scheduled_posts: Dict[str, Dict[str, Any]] = {}

class PostStates(StatesGroup):
    waiting_content = State()
    waiting_time = State()

def process_script_logic(text: str) -> list:
    code_lines = []
    in_code_block = False
    
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue
        
        is_code = in_code_block or any(k in line.lower() for k in ['loadstring', 'game:', 'local', 'function', 'http'])
        
        if is_code:
            if 'loadstring' in stripped and 'game:HttpGet' in stripped:
                if stripped.endswith(('()', '();')):
                    stripped = re.sub(r'\)(\(\))?;?$', f'("{WATERMARK_URL}")\\1', stripped)
            code_lines.append(stripped)
    
    return code_lines

def parse_content(text: str) -> Dict[str, Any]:
    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
    
    result = {'game': '', 'desc': '', 'key': False, 'code': []}
    
    if not lines:
        return result
    
    result['game'] = lines[0]
    
    code_start = None
    for i, line in enumerate(lines[1:], 1):
        lower = line.lower()
        if '#key' in lower or 'key+' in lower:
            result['key'] = True
        elif '#nokey' in lower or 'key-' in lower or 'no key' in lower:
            result['key'] = False
        elif code_start is None and any(k in lower for k in ['loadstring', 'game:', 'local ', 'function', '```']):
            code_start = i
            break
    
    if code_start and code_start > 1:
        result['desc'] = ' '.join([l for l in lines[1:code_start] if not l.startswith('#') and 'key' not in l.lower()])
    
    if code_start:
        result['code'] = process_script_logic('\n'.join(lines[code_start:]))
    
    return result

def format_post(game: str, desc: str, key: bool, code: list) -> str:
    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"🎮  {game.upper()}",
        "━━━━━━━━━━━━━━━━━━━",
        ""
    ]
    
    if desc:
        lines.extend([f"💬  {desc}", ""])
    
    lines.extend([
        f"{'🔐' if key else '🔓'}  {'Требуется ключ' if key else 'Ключ не нужен'}",
        ""
    ])
    
    if code:
        lines.extend(["⚡  СКРИПТ:", "```lua"] + code + ["```", ""])
    
    lines.extend(["━━━━━━━━━━━━━━━━━━━", f"📢  {CHANNEL}"])
    return '\n'.join(lines)

def get_channel_btn() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📢 Канал", url='https://t.me/RavionScripts')
    ]])

def get_main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Новый пост")],
        [KeyboardButton(text="📋 Очередь"), KeyboardButton(text="📊 Статус")]
    ], resize_keyboard=True)

def get_action_kb(post_id: str = None) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✅ Сейчас", callback_data='publish')],
        [InlineKeyboardButton(text="⏰ Отложить", callback_data='schedule')],
        [InlineKeyboardButton(text="✏️ Изменить", callback_data='edit')],
        [InlineKeyboardButton(text="❌ Отмена", callback_data='cancel')]
    ]
    
    if post_id:
        buttons.insert(1, [InlineKeyboardButton(text="🚀 Опубликовать немедленно", callback_data=f'pub_now_{post_id}')])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def parse_time(time_str: str) -> datetime | None:
    try:
        now = datetime.now()
        time_str = time_str.lower().strip()
        
        # 50м, 2ч, 1ч30м
        if 'ч' in time_str or 'м' in time_str or 'h' in time_str or 'm' in time_str:
            hours = minutes = 0
            h_match = re.search(r'(\d+)[чh]', time_str)
            m_match = re.search(r'(\d+)[мm]', time_str)
            if h_match:
                hours = int(h_match.group(1))
            if m_match:
                minutes = int(m_match.group(1))
            if hours or minutes:
                return now + timedelta(hours=hours, minutes=minutes)
        
        # 14:30
        time_match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
        if time_match:
            h, m = int(time_match.group(1)), int(time_match.group(2))
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            return target + timedelta(days=1) if target <= now else target
        
        # 02.11.2025 11:40 или 02.11 11:40
        dt_match = re.match(r'^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+(\d{1,2}):(\d{2})$', time_str)
        if dt_match:
            day, month = int(dt_match.group(1)), int(dt_match.group(2))
            year = int(dt_match.group(3)) if dt_match.group(3) else now.year
            hour, minute = int(dt_match.group(4)), int(dt_match.group(5))
            target = datetime(year, month, day, hour, minute)
            return target if target > now else None
        
        # Естественный язык (через 2 часа, завтра в 15:00, через 30 минут)
        parsed = dateparser.parse(
            time_str,
            languages=['ru', 'en'],
            settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now}
        )
        if parsed and parsed > now:
            return parsed
        
    except Exception as e:
        logger.error(f"Ошибка парсинга: {e}")
    
    return None

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if message.from_user.id not in ALLOWED_USERS:
        return
    
    user_data[message.from_user.id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'media': None, 'media_type': None}
    await state.clear()
    
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"📝 Формат:\n"
        f"Название игры\n"
        f"Описание\n"
        f"#key или #nokey\n"
        f"loadstring(...)\n\n"
        f"📸 Фото/видео — прикрепи\n"
        f"⏰ Время — любой формат\n\n"
        f"Примеры времени:\n"
        f"50м | 2ч | 14:30 | 02.11.2025 11:40",
        reply_markup=get_main_kb()
    )

@router.message(F.text == "➕ Новый пост")
async def new_post(message: Message, state: FSMContext):
    if message.from_user.id not in ALLOWED_USERS:
        return
    
    await state.set_state(PostStates.waiting_content)
    await message.answer(
        "📝 Отправь пост:\n\n"
        "Пример:\n"
        "Blox Fruits\n"
        "Лучший скрипт\n"
        "#key\n"
        "loadstring(game:HttpGet(\"url\"))()\n\n"
        "📸 Можно с фото/видео\n"
        "❌ /cancel отмена"
    )

@router.message(F.text == "📋 Очередь")
async def queue(message: Message):
    if message.from_user.id not in ALLOWED_USERS:
        return
    await show_queue(message, message.from_user.id)

@router.message(F.text == "📊 Статус")
async def status(message: Message):
    if message.from_user.id not in ALLOWED_USERS:
        return
    
    count = sum(1 for p in scheduled_posts.values() if p['user_id'] == message.from_user.id)
    await message.answer(
        f"📊 СТАТУС\n\n"
        f"⏰ В очереди: {count}\n"
        f"📢 Канал: {CHANNEL}\n"
        f"✅ Активен"
    )

@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    if message.from_user.id not in ALLOWED_USERS:
        return
    await state.clear()
    await message.answer("❌ Отменено", reply_markup=get_main_kb())

@router.message(PostStates.waiting_content)
async def process_content(message: Message, state: FSMContext):
    if message.from_user.id not in ALLOWED_USERS:
        return
    
    media_id = media_type = None
    text = ""
    
    if message.photo:
        media_id = message.photo[-1].file_id
        media_type = 'photo'
        text = message.caption or ""
    elif message.video:
        media_id = message.video.file_id
        media_type = 'video'
        text = message.caption or ""
    elif message.document and message.document.mime_type:
        if message.document.mime_type.startswith('image/'):
            media_id = message.document.file_id
            media_type = 'photo'
        elif message.document.mime_type.startswith('video/'):
            media_id = message.document.file_id
            media_type = 'video'
        text = message.caption or ""
    else:
        text = message.text or ""
    
    if not text.strip():
        await message.answer("⚠️ Отправь текст")
        return
    
    parsed = parse_content(text)
    
    if not parsed['game']:
        await message.answer("⚠️ Укажи название игры в первой строке")
        return
    
    user_data[message.from_user.id] = {
        'game': parsed['game'],
        'desc': parsed['desc'],
        'key': parsed['key'],
        'code': parsed['code'],
        'media': media_id,
        'media_type': media_type
    }
    
    await state.clear()
    await show_preview(message, message.from_user.id)

@router.message(PostStates.waiting_time)
async def process_time(message: Message, state: FSMContext):
    if message.from_user.id not in ALLOWED_USERS:
        return
    
    stime = parse_time(message.text)
    if not stime:
        await message.answer(
            "⚠️ Неверный формат\n\n"
            "Примеры:\n"
            "50м | 2ч30м | 14:30\n"
            "02.11 15:00 | 02.11.2025 11:40\n"
            "завтра в 10:00 | через 2 часа"
        )
        return
    
    uid = message.from_user.id
    pid = f"{uid}_{int(datetime.now().timestamp())}"
    d = user_data[uid]
    
    scheduled_posts[pid] = {
        'user_id': uid,
        'text': format_post(d['game'], d['desc'], d['key'], d['code']),
        'media': d.get('media'),
        'media_type': d.get('media_type'),
        'time': stime,
        'game': d['game'],
        'id': pid
    }
    
    asyncio.create_task(schedule_task(message.bot, pid))
    
    await message.answer(
        f"✅ ЗАПЛАНИРОВАНО\n\n"
        f"🎮 {d['game']}\n"
        f"⏰ {stime.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"📋 Очередь — смотри список",
        reply_markup=get_main_kb()
    )
    
    await state.clear()
    user_data[uid] = {'game': '', 'desc': '', 'key': False, 'code': [], 'media': None, 'media_type': None}

@router.callback_query(F.data == 'publish')
async def cb_publish(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await publish_now(callback.message, callback.from_user.id, callback.bot)
    await state.clear()
    await callback.answer()

@router.callback_query(F.data == 'schedule')
async def cb_schedule(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await state.set_state(PostStates.waiting_time)
    await callback.message.answer(
        "⏰ КОГДА ОПУБЛИКОВАТЬ?\n\n"
        "Примеры:\n"
        "50м — через 50 минут\n"
        "2ч — через 2 часа\n"
        "14:30 — сегодня в 14:30\n"
        "02.11.2025 11:40 — точная дата\n"
        "завтра в 10:00 — завтра"
    )
    await callback.answer()

@router.callback_query(F.data == 'edit')
async def cb_edit(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await state.set_state(PostStates.waiting_content)
    await callback.message.answer("✏️ Отправь новый контент")
    await callback.answer()

@router.callback_query(F.data == 'cancel')
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    user_data[callback.from_user.id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'media': None, 'media_type': None}
    await state.clear()
    await callback.message.answer("❌ Отменено", reply_markup=get_main_kb())
    await callback.answer()

@router.callback_query(F.data.startswith('del_'))
async def cb_delete(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    pid = callback.data.replace('del_', '')
    if pid in scheduled_posts:
        game = scheduled_posts[pid].get('game', 'Пост')
        del scheduled_posts[pid]
        await callback.answer(f"✅ {game} удалён", show_alert=True)
        await show_queue(callback.message, callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data.startswith('pub_now_'))
async def cb_pub_now(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    pid = callback.data.replace('pub_now_', '')
    if pid in scheduled_posts:
        post = scheduled_posts[pid]
        try:
            await send_to_channel(callback.bot, post)
            await callback.bot.send_message(
                post['user_id'],
                f"✅ ОПУБЛИКОВАНО\n\n🎮 {post['game']}\n📢 {CHANNEL}"
            )
            del scheduled_posts[pid]
            await callback.answer("✅ Опубликовано", show_alert=True)
            await show_queue(callback.message, callback.from_user.id)
        except Exception as e:
            await callback.answer(f"❌ Ошибка: {str(e)[:50]}", show_alert=True)
    else:
        await callback.answer("❌ Пост не найден", show_alert=True)

async def show_preview(message: Message, uid: int):
    d = user_data[uid]
    text = format_post(d['game'], d['desc'], d['key'], d['code'])
    preview = f"👀 ПРЕДПРОСМОТР\n\n{text}"
    
    try:
        if d.get('media_type') == 'photo':
            await message.answer_photo(d['media'], caption=preview, parse_mode='Markdown', reply_markup=get_action_kb())
        elif d.get('media_type') == 'video':
            await message.answer_video(d['media'], caption=preview, parse_mode='Markdown', reply_markup=get_action_kb())
        else:
            await message.answer(preview, parse_mode='Markdown', reply_markup=get_action_kb())
    except Exception as e:
        logger.error(f"Ошибка превью: {e}")
        await message.answer("⚠️ Ошибка отображения. Попробуй заново")

async def publish_now(message: Message, uid: int, bot: Bot):
    d = user_data[uid]
    text = format_post(d['game'], d['desc'], d['key'], d['code'])
    
    try:
        if d.get('media_type') == 'photo':
            await bot.send_photo(CHANNEL, d['media'], caption=text, parse_mode='Markdown', reply_markup=get_channel_btn())
        elif d.get('media_type') == 'video':
            await bot.send_video(CHANNEL, d['media'], caption=text, parse_mode='Markdown', reply_markup=get_channel_btn())
        else:
            await bot.send_message(CHANNEL, text, parse_mode='Markdown', reply_markup=get_channel_btn())
        
        await message.answer(
            f"✅ ОПУБЛИКОВАНО\n\n🎮 {d['game']}\n📢 {CHANNEL}",
            reply_markup=get_main_kb()
        )
        user_data[uid] = {'game': '', 'desc': '', 'key': False, 'code': [], 'media': None, 'media_type': None}
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await message.answer(f"❌ Ошибка: {str(e)[:100]}")

async def send_to_channel(bot: Bot, post: Dict):
    markup = get_channel_btn()
    if post.get('media_type') == 'photo':
        await bot.send_photo(CHANNEL, post['media'], caption=post['text'], parse_mode='Markdown', reply_markup=markup)
    elif post.get('media_type') == 'video':
        await bot.send_video(CHANNEL, post['media'], caption=post['text'], parse_mode='Markdown', reply_markup=markup)
    else:
        await bot.send_message(CHANNEL, post['text'], parse_mode='Markdown', reply_markup=markup)

async def schedule_task(bot: Bot, pid: str):
    while pid in scheduled_posts:
        post = scheduled_posts[pid]
        if datetime.now() >= post['time']:
            try:
                await send_to_channel(bot, post)
                await bot.send_message(post['user_id'], f"✅ ОПУБЛИКОВАНО\n\n🎮 {post['game']}\n📢 {CHANNEL}")
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                await bot.send_message(post['user_id'], f"❌ Ошибка: {str(e)[:100]}")
            
            if pid in scheduled_posts:
                del scheduled_posts[pid]
            break
        await asyncio.sleep(30)

async def show_queue(message: Message, uid: int):
    posts = {k: v for k, v in scheduled_posts.items() if v['user_id'] == uid}
    
    if not posts:
        await message.answer("📭 Очередь пуста")
        return
    
    text = "📅 ЗАПЛАНИРОВАНО\n\n"
    kb = []
    
    for pid, p in sorted(posts.items(), key=lambda x: x[1]['time']):
        delta = p['time'] - datetime.now()
        h, m = int(delta.total_seconds() // 3600), int((delta.total_seconds() % 3600) // 60)
        time_str = p['time'].strftime('%d.%m в %H:%M')
        countdown = f"через {h}ч {m}м" if h > 0 else f"через {m}м"
        
        text += f"🎮 {p['game']}\n⏰ {time_str} ({countdown})\n\n"
        kb.append([
            InlineKeyboardButton(text=f"🚀 {p['game']}", callback_data=f'pub_now_{pid}'),
            InlineKeyboardButton(text="❌", callback_data=f'del_{pid}')
        ])
    
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

async def main():
    logger.info("🚀 Старт")
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info(f"✅ Работаю | {CHANNEL}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=['message', 'callback_query'])

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⛔ Стоп")
    except Exception as e:
        logger.error(f"💥 ERROR: {e}")
