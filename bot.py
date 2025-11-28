import logging
import re
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any

try:
    from aiogram import Bot, Dispatcher, F, Router
    from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
    from aiogram.filters import Command, CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.fsm.storage.memory import MemoryStorage
except ImportError:
    print("Установите aiogram: pip install -r requirements.txt")
    exit(1)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"
CHANNEL = "@RavionScripts"
WATERMARK_URL = "https://t.me/RavionScripts"
ADMIN_ID = 7637946765
MODERATOR_ID = 6510703948
ALLOWED_USERS = {ADMIN_ID, MODERATOR_ID}

user_data: Dict[int, Dict[str, Any]] = {}
scheduled_posts: Dict[str, Dict[str, Any]] = {}

class PostStates(StatesGroup):
    content = State()
    time = State()

def process_script(text: str) -> list:
    code_lines = []
    in_block = False
    
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_block = not in_block
            continue
            
        is_code = in_block or any(k in line.lower() for k in ['loadstring', 'game:', 'local', 'function', 'http'])
        
        if is_code:
            if 'loadstring' in stripped and 'game:HttpGet' in stripped:
                if stripped.endswith('()'):
                    stripped = stripped[:-2] + f'("{WATERMARK_URL}")'
                elif stripped.endswith('();'):
                    stripped = stripped[:-3] + f'("{WATERMARK_URL}");'
            code_lines.append(stripped)
            
    return code_lines

def parse_content(text: str, photo_id: str = None) -> Dict[str, Any]:
    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
    
    result = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': photo_id}
    
    if not lines:
        return result
    
    result['game'] = lines[0]
    code_start = None
    
    for i, line in enumerate(lines[1:], 1):
        lower = line.lower()
        
        if '#key' in lower or 'key+' in lower:
            result['key'] = True
            continue
        elif '#nokey' in lower or 'key-' in lower:
            result['key'] = False
            continue
        
        if any(k in lower for k in ['loadstring', 'game:', 'local ', 'function']):
            code_start = i
            break
    
    if code_start and code_start > 1:
        desc_lines = [lines[i] for i in range(1, code_start) if not lines[i].startswith('#')]
        result['desc'] = ' '.join(desc_lines)
    
    if code_start:
        result['code'] = process_script('\n'.join(lines[code_start:]))
    
    return result

def format_post(game: str, desc: str, key: bool, code: list) -> str:
    parts = [f"🎮 {game.upper()}\n"]
    
    if desc:
        parts.append(f"{desc}\n")
    
    parts.append("🔐 Ключ: Да\n" if key else "🔓 Ключ: Нет\n")
    
    if code:
        parts.append("```lua\n" + '\n'.join(code) + "\n```\n")
    
    parts.append(f"💎 {CHANNEL}")
    return '\n'.join(parts)

def parse_time(time_str: str) -> datetime | None:
    try:
        now = datetime.now()
        time_str = time_str.lower().strip()
        
        if match := re.match(r'^(\d{1,2}):(\d{2})$', time_str):
            hour, minute = int(match.group(1)), int(match.group(2))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target + timedelta(days=1) if target <= now else target
        
        if 'ч' in time_str or 'м' in time_str:
            hours = int(m.group(1)) if (m := re.search(r'(\d+)ч', time_str)) else 0
            minutes = int(m.group(1)) if (m := re.search(r'(\d+)м', time_str)) else 0
            if hours or minutes:
                return now + timedelta(hours=hours, minutes=minutes)
        
        if 'завтра' in time_str and (match := re.search(r'(\d{1,2}):(\d{2})', time_str)):
            return (now + timedelta(days=1)).replace(
                hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0
            )
    except Exception as e:
        logger.error(f"Parse time error: {e}")
    return None

router = Router()

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    if message.from_user.id not in ALLOWED_USERS:
        return
    
    user_data[message.from_user.id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
    await state.clear()
    
    await message.answer(
        "Формат:\n"
        "Название\n"
        "Описание\n"
        "#key или #nokey\n"
        "код\n\n"
        "Можно добавить фото.\n"
        "/queue - очередь\n"
        "/help - помощь"
    )

@router.message(Command("help"))
async def help_cmd(message: Message):
    if message.from_user.id not in ALLOWED_USERS:
        return
    
    await message.answer(
        "Пример:\n"
        "Blox Fruits\n"
        "Описание скрипта\n"
        "#key\n"
        "loadstring(game:HttpGet('url'))()\n\n"
        "Время:\n"
        "14:30 или 2ч или 30м"
    )

@router.message(Command("queue"))
async def queue_cmd(message: Message):
    if message.from_user.id not in ALLOWED_USERS:
        return
    await show_scheduled(message, message.from_user.id)

@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    if message.from_user.id not in ALLOWED_USERS:
        return
    await state.clear()
    await message.answer("Отменено")

@router.message(F.photo | F.text)
async def handle_message(message: Message, state: FSMContext):
    if message.from_user.id not in ALLOWED_USERS:
        return
    
    current_state = await state.get_state()
    
    if current_state == PostStates.time.state:
        stime = parse_time(message.text)
        if not stime:
            await message.answer("Неверный формат. Примеры: 14:30, 2ч, 30м")
            return
        
        uid = message.from_user.id
        pid = f"{uid}_{int(datetime.now().timestamp())}"
        d = user_data[uid]
        
        scheduled_posts[pid] = {
            'user_id': uid,
            'text': format_post(d['game'], d['desc'], d['key'], d['code']),
            'photo': d.get('photo'),
            'time': stime,
            'game': d['game']
        }
        
        asyncio.create_task(schedule_task(message.bot, pid))
        
        await message.answer(f"✅ {d['game']}\n⏰ {stime.strftime('%d.%m %H:%M')}")
        await state.clear()
        user_data[uid] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
        return
    
    photo_id = message.photo[-1].file_id if message.photo else None
    text = (message.caption or message.text or "").strip()
    
    if not text:
        await message.answer("Отправьте текст")
        return
    
    parsed = parse_content(text, photo_id)
    
    if not parsed['game']:
        await message.answer("Не найдено название игры")
        return
    
    user_data[message.from_user.id] = parsed
    await show_preview(message, message.from_user.id)

@router.callback_query(F.data == 'publish')
async def cb_publish(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALLOWED_USERS:
        return
    
    await publish(callback.message, callback.from_user.id, callback.bot)
    await state.clear()
    await callback.answer()

@router.callback_query(F.data == 'schedule')
async def cb_schedule(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALLOWED_USERS:
        return
    
    await state.set_state(PostStates.time)
    await callback.message.answer("Когда опубликовать?\n14:30, 2ч, 30м, завтра 10:00")
    await callback.answer()

@router.callback_query(F.data == 'cancel')
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALLOWED_USERS:
        return
    
    user_data[callback.from_user.id] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
    await state.clear()
    await callback.message.answer("Отменено")
    await callback.answer()

@router.callback_query(F.data.startswith('del_'))
async def cb_delete(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        return
    
    pid = callback.data.replace('del_', '')
    if pid in scheduled_posts:
        del scheduled_posts[pid]
        await callback.answer("Удалено")
        await show_scheduled(callback.message, callback.from_user.id)
    await callback.answer()

async def show_preview(message: Message, uid: int):
    d = user_data[uid]
    text = format_post(d['game'], d['desc'], d['key'], d['code'])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data='publish')],
        [InlineKeyboardButton(text="⏰ Отложить", callback_data='schedule')],
        [InlineKeyboardButton(text="❌ Отменить", callback_data='cancel')]
    ])
    
    try:
        if d.get('photo'):
            await message.answer_photo(photo=d['photo'], caption=text, parse_mode='Markdown', reply_markup=kb)
        else:
            await message.answer(text, parse_mode='Markdown', reply_markup=kb)
    except Exception as e:
        logger.error(f"Preview error: {e}")
        await message.answer("Ошибка отображения")

async def publish(message: Message, uid: int, bot: Bot):
    d = user_data[uid]
    text = format_post(d['game'], d['desc'], d['key'], d['code'])
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Канал", url='https://t.me/RavionScripts')
    ]])
    
    try:
        if d.get('photo'):
            await bot.send_photo(CHANNEL, photo=d['photo'], caption=text, parse_mode='Markdown', reply_markup=markup)
        else:
            await bot.send_message(CHANNEL, text=text, parse_mode='Markdown', reply_markup=markup)
        
        await message.answer(f"✅ {d['game']}")
        user_data[uid] = {'game': '', 'desc': '', 'key': False, 'code': [], 'photo': None}
    except Exception as e:
        logger.error(f"Publish error: {e}")
        await message.answer(f"Ошибка: {e}")

async def schedule_task(bot: Bot, pid: str):
    while pid in scheduled_posts:
        post = scheduled_posts[pid]
        if datetime.now() >= post['time']:
            try:
                markup = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🚀 Канал", url='https://t.me/RavionScripts')
                ]])
                
                if post.get('photo'):
                    await bot.send_photo(CHANNEL, photo=post['photo'], caption=post['text'], 
                                       parse_mode='Markdown', reply_markup=markup)
                else:
                    await bot.send_message(CHANNEL, text=post['text'], parse_mode='Markdown', reply_markup=markup)
                
                await bot.send_message(post['user_id'], f"✅ {post['game']}")
            except Exception as e:
                logger.error(f"Schedule error: {e}")
                await bot.send_message(post['user_id'], f"Ошибка: {e}")
            
            if pid in scheduled_posts:
                del scheduled_posts[pid]
            break
        await asyncio.sleep(30)

async def show_scheduled(message: Message, uid: int):
    posts = {k: v for k, v in scheduled_posts.items() if v['user_id'] == uid}
    
    if not posts:
        await message.answer("Очередь пуста")
        return

    text = "📋 Очередь:\n\n"
    kb = []
    
    for pid, p in sorted(posts.items(), key=lambda x: x[1]['time']):
        time_left = p['time'] - datetime.now()
        hours = int(time_left.total_seconds() / 3600)
        minutes = int((time_left.total_seconds() % 3600) / 60)
        
        countdown = f"{hours}ч {minutes}м" if hours > 0 else f"{minutes}м"
        game = p.get('game', 'Пост')
        
        text += f"{game}\n{p['time'].strftime('%d.%m %H:%M')} (через {countdown})\n\n"
        kb.append([InlineKeyboardButton(text=f"❌ {game}", callback_data=f'del_{pid}')])
    
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

async def main():
    logger.info("Запуск...")
    
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    logger.info(f"Готов. Канал: {CHANNEL}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=['message', 'callback_query'])

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Стоп")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
