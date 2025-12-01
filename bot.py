import logging
import re
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

try:
    from aiogram import Bot, Dispatcher, F, Router
    from aiogram.types import (
        Message, 
        CallbackQuery, 
        InlineKeyboardButton, 
        InlineKeyboardMarkup,
        ReplyKeyboardMarkup,
        KeyboardButton
    )
    from aiogram.filters import Command, CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.fsm.storage.memory import MemoryStorage
except ImportError:
    print("CRITICAL: Установите библиотеку: pip install aiogram")
    exit(1)

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"
CHANNEL = "@RavionScripts"
WATERMARK_URL = "https://t.me/RavionScripts"
ADMIN_IDS = {7637946765, 6510703948}  # ID админов

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

user_data: Dict[int, Dict[str, Any]] = {}
scheduled_posts: Dict[str, Dict[str, Any]] = {}

class PostStates(StatesGroup):
    waiting_content = State()
    waiting_time = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def check_access(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def parse_time(time_str: str) -> Optional[datetime]:
    now = datetime.now()
    text = time_str.lower().strip().replace('  ', ' ')
    
    try:
        # 1. Формат "02.11.2025 11:40" или "02.11 11:40"
        date_match = re.search(r'(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s+(\d{1,2}):(\d{2})', text)
        if date_match:
            d, m = int(date_match.group(1)), int(date_match.group(2))
            y = int(date_match.group(3)) if date_match.group(3) else now.year
            if y < 100: y += 2000
            h, mn = int(date_match.group(4)), int(date_match.group(5))
            return datetime(y, m, d, h, mn)

        # 2. Формат "11:40" (сегодня или завтра)
        time_match = re.search(r'^(\d{1,2}):(\d{2})$', text)
        if time_match:
            h, mn = int(time_match.group(1)), int(time_match.group(2))
            target = now.replace(hour=h, minute=mn, second=0)
            if target < now: target += timedelta(days=1)
            return target

        # 3. Относительное "50м", "1ч 20м", "через 2 часа"
        delta_m = 0
        
        # Поиск часов
        h_search = re.search(r'(\d+)\s*(ч|h|час)', text)
        if h_search: delta_m += int(h_search.group(1)) * 60
        
        # Поиск минут
        m_search = re.search(r'(\d+)\s*(м|m|мин)', text)
        if m_search: delta_m += int(m_search.group(1))

        if delta_m > 0:
            return now + timedelta(minutes=delta_m)

    except Exception:
        return None
    return None

def process_script_logic(text: str) -> list:
    code_lines = []
    in_code = False
    for line in text.split('\n'):
        s = line.strip()
        if s.startswith('```'):
            in_code = not in_code
            continue
        
        # Авто-добавление ватермарки
        if ('loadstring' in s or 'getgenv' in s) and 'game:HttpGet' in s:
            if WATERMARK_URL not in s:
                if s.endswith('()'): s = s[:-2] + f'("{WATERMARK_URL}")'
                elif s.endswith('();'): s = s[:-3] + f'("{WATERMARK_URL}");'
        
        code_lines.append(s)
    return code_lines

def parse_post_content(text: str) -> Dict:
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    res = {'game': 'Unknown', 'desc': '', 'key': False, 'code': []}
    
    if not lines: return res
    
    res['game'] = lines[0] # Первая строка - игра
    
    code_start = -1
    for i, line in enumerate(lines[1:], 1):
        low = line.lower()
        if '#key' in low or 'key+' in low: res['key'] = True; continue
        if '#nokey' in low or 'key-' in low: res['key'] = False; continue
        
        if code_start == -1 and any(x in low for x in ['loadstring', 'game:', 'function', 'local ', 'getgenv']):
            code_start = i
            break
            
    if code_start != -1:
        # Описание между заголовком и кодом
        desc_lines = [l for l in lines[1:code_start] if not l.startswith('#')]
        res['desc'] = '\n'.join(desc_lines)
        res['code'] = process_script_logic('\n'.join(lines[code_start:]))
    else:
        # Если код не найден явно, считаем все после заголовка описанием (или кодом если коротко)
        desc_part = [l for l in lines[1:] if not l.startswith('#')]
        res['desc'] = '\n'.join(desc_part)

    return res

def format_post_text(data: Dict) -> str:
    parts = [
        "━━━━━━━━━━━━━━━━━━━",
        f"🎮  {data['game'].upper()}",
        "━━━━━━━━━━━━━━━━━━━\n"
    ]
    
    if data['desc']: parts.append(f"💬  {data['desc']}\n")
    
    key_txt = "🔐 Требуется ключ" if data['key'] else "🔓 Ключ не нужен"
    parts.append(f"{key_txt}\n")
    
    if data['code']:
        parts.append("⚡  СКРИПТ:")
        parts.append("```lua")
        parts.extend(data['code'])
        parts.append("```\n")
        
    parts.append("━━━━━━━━━━━━━━━━━━━")
    parts.append(f"📢  {CHANNEL}")
    return "\n".join(parts)

# --- КЛАВИАТУРЫ ---

def kb_main():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Новый пост")],
        [KeyboardButton(text="📋 Мои посты"), KeyboardButton(text="📊 Статистика")]
    ], resize_keyboard=True)

def kb_actions():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data='pub_now')],
        [InlineKeyboardButton(text="⏰ Отложить", callback_data='schedule')],
        [InlineKeyboardButton(text="✏️ Ред.", callback_data='edit'), InlineKeyboardButton(text="❌ Отмена", callback_data='cancel')]
    ])

def kb_link():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Скрипт в канале", url='[https://t.me/RavionScripts](https://t.me/RavionScripts)')]])

# --- ЛОГИКА БОТА ---

router = Router()

@router.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    if not check_access(msg.from_user.id): return
    await state.clear()
    await msg.answer("👋 Ку. Кидай текст поста или фото/видео с описанием.\n\nФормат:\nИгра\nОписание\n#key\nСкрипт", reply_markup=kb_main())

@router.message(F.text == "➕ Новый пост")
async def new_post_handler(msg: Message, state: FSMContext):
    if not check_access(msg.from_user.id): return
    await state.set_state(PostStates.waiting_content)
    await msg.answer("📝 Жду контент (текст, фото или видео).")

@router.message(PostStates.waiting_content)
async def content_handler(msg: Message, state: FSMContext):
    user_id = msg.from_user.id
    
    # Обработка медиа (Фото или Видео)
    media_id = None
    media_type = None
    text = msg.text or ""
    
    if msg.photo:
        media_id = msg.photo[-1].file_id
        media_type = 'photo'
        text = msg.caption or ""
    elif msg.video:
        media_id = msg.video.file_id
        media_type = 'video'
        text = msg.caption or ""
    elif msg.document and 'image' in msg.document.mime_type:
        media_id = msg.document.file_id
        media_type = 'photo'
        text = msg.caption or ""

    if not text.strip() and not media_id:
        await msg.answer("⚠️ Пустое сообщение.")
        return

    parsed = parse_post_content(text)
    
    user_data[user_id] = {
        **parsed,
        'media_id': media_id,
        'media_type': media_type
    }
    
    preview = format_post_text(user_data[user_id])
    
    try:
        if media_type == 'photo':
            await msg.answer_photo(media_id, caption=preview, parse_mode='Markdown', reply_markup=kb_actions())
        elif media_type == 'video':
            await msg.answer_video(media_id, caption=preview, parse_mode='Markdown', reply_markup=kb_actions())
        else:
            await msg.answer(preview, parse_mode='Markdown', reply_markup=kb_actions())
    except Exception as e:
        await msg.answer(f"⚠️ Ошибка формата Markdown: {e}")

@router.callback_query(F.data == 'pub_now')
async def publish_now(cb: CallbackQuery, state: FSMContext):
    user_id = cb.from_user.id
    data = user_data.get(user_id)
    if not data: return await cb.answer("❌ Данные устарели", show_alert=True)
    
    text = format_post_text(data)
    try:
        if data['media_type'] == 'photo':
            await cb.bot.send_photo(CHANNEL, data['media_id'], caption=text, parse_mode='Markdown', reply_markup=kb_link())
        elif data['media_type'] == 'video':
            await cb.bot.send_video(CHANNEL, data['media_id'], caption=text, parse_mode='Markdown', reply_markup=kb_link())
        else:
            await cb.bot.send_message(CHANNEL, text, parse_mode='Markdown', reply_markup=kb_link())
        
        await cb.message.delete()
        await cb.message.answer("✅ Опубликовано!", reply_markup=kb_main())
        await state.clear()
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)

@router.callback_query(F.data == 'schedule')
async def ask_time(cb: CallbackQuery, state: FSMContext):
    await state.set_state(PostStates.waiting_time)
    await cb.message.answer(
        "⏰ **Напиши время публикации:**\n\n"
        "• `14:30` (сегодня/завтра)\n"
        "• `05.11 18:00` (дата)\n"
        "• `30м` (через 30 минут)\n"
        "• `2ч` (через 2 часа)", 
        parse_mode='Markdown'
    )
    await cb.answer()

@router.message(PostStates.waiting_time)
async def schedule_handler(msg: Message, state: FSMContext):
    target_time = parse_time(msg.text)
    if not target_time:
        return await msg.answer("⚠️ Не понял время. Попробуй: `15:30` или `1ч`")
    
    user_id = msg.from_user.id
    data = user_data.get(user_id)
    pid = f"{user_id}_{int(datetime.now().timestamp())}"
    
    scheduled_posts[pid] = {
        'data': data,
        'time': target_time,
        'user_id': user_id
    }
    
    asyncio.create_task(wait_and_publish(msg.bot, pid))
    
    await msg.answer(f"✅ Отложено на: **{target_time.strftime('%d.%m %H:%M')}**", parse_mode='Markdown', reply_markup=kb_main())
    await state.clear()

@router.message(F.text == "📋 Мои посты")
async def show_scheduled(msg: Message):
    user_id = msg.from_user.id
    user_posts = {k: v for k, v in scheduled_posts.items() if v['user_id'] == user_id}
    
    if not user_posts:
        return await msg.answer("📭 Очередь пуста.")
        
    txt = "📅 **Очередь публикаций:**\n\n"
    kb = []
    
    for pid, item in sorted(user_posts.items(), key=lambda x: x[1]['time']):
        t_str = item['time'].strftime('%d.%m %H:%M')
        game = item['data']['game']
        txt += f"🎮 {game} — ⏰ {t_str}\n"
        # Кнопки управления для каждого поста
        kb.append([
            InlineKeyboardButton(text=f"🚀 Запостить {game}", callback_data=f"force_{pid}"),
            InlineKeyboardButton(text=f"🗑 Удалить", callback_data=f"del_{pid}")
        ])
        
    await msg.answer(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode='Markdown')

# Обработчик мгновенной публикации отложенного поста
@router.callback_query(F.data.startswith('force_'))
async def force_publish(cb: CallbackQuery):
    pid = cb.data.split('_')[1]
    post = scheduled_posts.get(pid)
    if not post: return await cb.answer("❌ Пост не найден", show_alert=True)
    
    # Меняем время на "сейчас", цикл публикации подхватит его почти мгновенно
    post['time'] = datetime.now() - timedelta(seconds=1)
    await cb.answer("🚀 Отправляю в очередь на мгновенную публикацию...")
    await cb.message.delete()

@router.callback_query(F.data.startswith('del_'))
async def delete_post(cb: CallbackQuery):
    pid = cb.data.split('_')[1]
    if pid in scheduled_posts:
        del scheduled_posts[pid]
        await cb.answer("🗑 Удалено")
        await cb.message.delete()
    else:
        await cb.answer("Уже удалено")

@router.callback_query(F.data == 'cancel')
async def cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.delete()
    await cb.message.answer("❌ Отмена", reply_markup=kb_main())

# Фоновая задача
async def wait_and_publish(bot: Bot, pid: str):
    while pid in scheduled_posts:
        post = scheduled_posts[pid]
        if datetime.now() >= post['time']:
            data = post['data']
            text = format_post_text(data)
            try:
                if data['media_type'] == 'photo':
                    await bot.send_photo(CHANNEL, data['media_id'], caption=text, parse_mode='Markdown', reply_markup=kb_link())
                elif data['media_type'] == 'video':
                    await bot.send_video(CHANNEL, data['media_id'], caption=text, parse_mode='Markdown', reply_markup=kb_link())
                else:
                    await bot.send_message(CHANNEL, text, parse_mode='Markdown', reply_markup=kb_link())
                
                await bot.send_message(post['user_id'], f"✅ Пост **{data['game']}** опубликован!", parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Error publishing: {e}")
                await bot.send_message(post['user_id'], f"❌ Ошибка публикации: {e}")
            
            if pid in scheduled_posts: del scheduled_posts[pid]
            break
        await asyncio.sleep(10)

async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
