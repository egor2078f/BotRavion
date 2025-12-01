import logging
import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Union, Optional

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

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8254879975:AAF-ikyNFF3kUeZWBT0pwbq-YnqWRxNIv20"
CHANNEL_ID = "@RavionScripts"
WATERMARK_LINK = "https://t.me/RavionScripts"
# ID всех админов
ADMINS = {7637946765, 6510703948} 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- ХРАНИЛИЩЕ ---
scheduled_posts: Dict[str, Dict[str, Any]] = {}
instruction_messages: Dict[int, int] = {}

class Form(StatesGroup):
    waiting_content = State() # Обычный пост
    waiting_steal = State()   # Режим кражи
    waiting_time = State()    # Выбор времени

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def clean_stolen_text(text: str) -> str:
    """Удаляет рекламу, ссылки на чужие каналы и мусор."""
    # Удаляем ссылки t.me/...
    text = re.sub(r't\.me\/[a-zA-Z0-9_]+', '', text)
    text = re.sub(r'@\w+', '', text) # Удаляем упоминания @channel
    # Удаляем строки с призывами подписаться
    lines = text.split('\n')
    clean_lines = []
    for line in lines:
        low = line.lower()
        if any(x in low for x in ['подпишись', 'subscribe', 'join', 'канал', 'channel', 'credits']):
            continue
        clean_lines.append(line)
    return "\n".join(clean_lines).strip()

def parse_content(raw_text: str, is_stolen: bool = False) -> Dict[str, Any]:
    # Если это кража, сначала чистим текст от мусора
    if is_stolen:
        # Пытаемся сохранить код нетронутым, чистим только описание
        parts = raw_text.split('```')
        desc_part = clean_stolen_text(parts[0])
        # Собираем обратно, но грубо. Лучше разберем построчно.
    
    lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
    res = {'game': '🎮 Game', 'desc': '', 'key': False, 'code': []}
    
    if not lines: return res
    
    # Эвристика: Первая строка - это часто название игры
    # Если строка короткая (< 40 символов) и не код - берем как заголовок
    first_line = lines[0]
    if len(first_line) < 40 and "```" not in first_line and "loadstring" not in first_line.lower():
        res['game'] = clean_stolen_text(first_line) if is_stolen else first_line
        lines = lines[1:]
    
    code_found = False
    desc_lines = []
    
    for line in lines:
        low = line.lower()
        # Поиск флагов ключа
        if '#key' in low or 'key+' in low: res['key'] = True; continue
        if '#nokey' in low or 'key-' in low or 'no key' in low: res['key'] = False; continue
        
        # Определение кода
        is_code_start = any(x in low for x in ['loadstring', 'game:', 'function', 'local ', 'getgenv', '```'])
        
        if not code_found and is_code_start:
            code_found = True
            clean = line.replace('```lua', '').replace('```', '')
            # Заменяем чужой loadstring на наш watermark, если это просто ссылка
            if 'game:HttpGet' in clean and WATERMARK_LINK not in clean:
                # Если крадем пост, стараемся вставить наш копирайт в скрипт
                if is_stolen:
                     pass # Тут можно добавить сложную логику замены ссылок
            res['code'].append(clean)
        elif code_found:
            # Если начался код, все последующее считаем кодом, пока не встретим закрытие (упрощенно)
            res['code'].append(line.replace('```', ''))
        else:
            if not line.startswith('#'): 
                clean_line = clean_stolen_text(line) if is_stolen else line
                if clean_line: desc_lines.append(clean_line)
    
    res['desc'] = '\n'.join(desc_lines)
    return res

def build_post_text(data: Dict) -> str:
    game = html_escape(data['game']).upper()
    desc = html_escape(data['desc'])
    
    # Шапка
    text = f"<b>━━━━━━━━━━━━━━━━━━━</b>\n🎮 <b>{game}</b>\n<b>━━━━━━━━━━━━━━━━━━━</b>\n\n"
    
    # Описание в красивой цитате
    if desc: 
        text += f"<blockquote>{desc}</blockquote>\n\n"
    
    # Статус ключа
    text += "🔐 <b>Требуется ключ</b>\n\n" if data['key'] else "🔓 <b>Ключ не нужен</b>\n\n"
    
    # Код
    if data['code']:
        code = "\n".join(data['code'])
        # Чистим код от лишних пустых строк в начале/конце
        code = code.strip()
        text += f"⚡ <b>СКРИПТ:</b>\n<pre><code class=\"language-lua\">{html_escape(code)}</code></pre>\n\n"
    
    # Подвал
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
        [KeyboardButton(text="➕ Новый пост"), KeyboardButton(text="🥷 Украсть пост")],
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
        "Выбери действие:",
        reply_markup=kb_main(), parse_mode=ParseMode.HTML
    )

# --- ОБЩАЯ ФУНКЦИЯ ПРОВЕРКИ МЕНЮ ---
async def check_menu_click(msg: Message, state: FSMContext) -> bool:
    if msg.text == "👤 Профиль":
        await state.clear()
        await profile(msg)
        return True
    if msg.text == "➕ Новый пост":
        await new_post(msg, state)
        return True
    if msg.text == "🥷 Украсть пост":
        await steal_post_start(msg, state)
        return True
    return False

# --- ОБРАБОТЧИКИ СОЗДАНИЯ ПОСТА ---

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
    info = await msg.answer(
        "📝 <b>Создание поста</b>\nПришли фото/видео + текст.\nПример:\n"
        f"<code>{example}</code>", parse_mode=ParseMode.HTML
    )
    instruction_messages[msg.chat.id] = info.message_id
    await state.set_state(Form.waiting_content)

@router.message(F.text == "🥷 Украсть пост")
async def steal_post_start(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.clear()
    
    info = await msg.answer(
        "🥷 <b>Режим кражи контента</b>\n\n"
        "Перешли сюда пост из другого канала или скопируй текст.\n"
        "Я автоматически:\n"
        "1. Удалю чужие ссылки\n"
        "2. Найду скрипт\n"
        "3. Оформлю под наш стиль\n\n"
        "⏳ Жду сообщение...",
        parse_mode=ParseMode.HTML
    )
    instruction_messages[msg.chat.id] = info.message_id
    await state.set_state(Form.waiting_steal)

@router.message(Form.waiting_content)
async def process_content(msg: Message, state: FSMContext):
    if await check_menu_click(msg, state): return
    await process_post_input(msg, state, is_stolen=False)

@router.message(Form.waiting_steal)
async def process_steal(msg: Message, state: FSMContext):
    if await check_menu_click(msg, state): return
    await process_post_input(msg, state, is_stolen=True)

async def process_post_input(msg: Message, state: FSMContext, is_stolen: bool):
    # Удаляем инструкцию
    if msg.chat.id in instruction_messages:
        try:
            await msg.bot.delete_message(msg.chat.id, instruction_messages[msg.chat.id])
            del instruction_messages[msg.chat.id]
        except: pass

    ctype = 'text'
    fid = None
    # Берем текст из тела или из подписи (caption)
    text = msg.text or msg.caption or ""
    
    # Определяем тип медиа
    if msg.photo: ctype, fid = 'photo', msg.photo[-1].file_id
    elif msg.video: ctype, fid = 'video', msg.video.file_id
    elif msg.animation: ctype, fid = 'animation', msg.animation.file_id
    elif msg.document: ctype, fid = 'document', msg.document.file_id
    
    if not text.strip() and ctype == 'text':
        return await msg.answer("⚠️ Пустое сообщение. Где контент?")
        
    parsed = parse_content(text, is_stolen=is_stolen)
    
    # Если украли и не нашли название, ставим заглушку
    if is_stolen and parsed['game'] == '🎮 Game':
        parsed['game'] = "⚙️ СКРИПТ"

    await state.update_data(ctype=ctype, fid=fid, parsed=parsed)
    
    preview = build_post_text(parsed)
    try:
        kwargs = {"caption": preview, "parse_mode": ParseMode.HTML, "reply_markup": kb_preview()}
        
        if ctype == 'photo': await msg.answer_photo(fid, **kwargs)
        elif ctype == 'video': await msg.answer_video(fid, **kwargs)
        elif ctype == 'animation': await msg.answer_animation(fid, **kwargs)
        elif ctype == 'document': await msg.answer_document(fid, **kwargs)
        else: await msg.answer(preview, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb_preview())
    except Exception as e:
        await msg.answer(f"❌ Ошибка форматирования: {e}")

# --- CALLBACKS ---

@router.callback_query(F.data == "cancel")
async def cancel_post(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.delete()
    await cb.answer("❌ Отменено")

@router.callback_query(F.data == "ignore")
async def ignore_click(cb: CallbackQuery):
    await cb.answer("🔒 Нет прав", show_alert=True)

@router.callback_query(F.data == "pub_now")
async def pub_now(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data:
        await cb.message.delete()
        return await cb.answer("❌ Данные устарели", show_alert=True)

    await publish_post(cb.bot, data)
    await state.clear()
    await cb.message.delete()
    await cb.answer("✅ Опубликовано")

@router.callback_query(F.data == "schedule")
async def schedule_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_time)
    await cb.message.delete()
    await cb.message.answer("⏰ Введи время (пример: `1ч` или `18:00`)", parse_mode=ParseMode.HTML)

@router.message(Form.waiting_time)
async def schedule_finish(msg: Message, state: FSMContext):
    if await check_menu_click(msg, state): return

    t = parse_time(msg.text)
    if not t: return await msg.answer("⚠️ Не понял время.")
    
    data = await state.get_data()
    if not data:
        await state.clear()
        return await msg.answer("❌ Ошибка данных.")

    pid = f"{msg.from_user.id}_{int(datetime.now().timestamp())}"
    
    scheduled_posts[pid] = {
        'data': data,
        'time': t,
        'creator_id': msg.from_user.id,
        'creator_name': msg.from_user.first_name
    }
    
    await state.clear()
    await msg.answer(
        f"✅ <b>Пост запланирован!</b>\n⏰ {t.strftime('%d.%m %H:%M')}", 
        parse_mode=ParseMode.HTML, reply_markup=kb_main()
    )

# --- ПРОФИЛЬ И ОЧЕРЕДЬ ---

@router.message(F.text == "👤 Профиль")
async def profile(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    uid = msg.from_user.id
    my_posts = sum(1 for p in scheduled_posts.values() if p['creator_id'] == uid)
    total = len(scheduled_posts)
    
    text = (
        f"👨‍💻 <b>Админ: {msg.from_user.first_name}</b>\n"
        f"🆔 <code>{uid}</code>\n"
        f"📦 Твоих постов: <b>{my_posts}</b>\n"
        f"🌐 Всего в очереди: <b>{total}</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Открыть очередь", callback_data="view_queue")]
    ])
    await msg.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

@router.callback_query(F.data == "view_queue")
async def view_queue(cb: CallbackQuery):
    if not scheduled_posts:
        return await cb.answer("📭 Очередь пуста", show_alert=True)
    
    user_id = cb.from_user.id
    sorted_posts = sorted(scheduled_posts.items(), key=lambda x: x[1]['time'])
    
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
    try:
        action, pid = cb.data.split("_", 1)
    except ValueError:
        return await cb.answer("❌ Ошибка данных кнопки", show_alert=True)
    
    post = scheduled_posts.get(pid)
    if not post: 
        await cb.message.delete()
        return await cb.answer("❌ Пост уже не существует", show_alert=True)
        
    if post['creator_id'] != cb.from_user.id:
        return await cb.answer("⛔ Это не твой пост!", show_alert=True)
        
    if action == "del":
        del scheduled_posts[pid]
        await cb.message.delete()
        await cb.answer("🗑 Пост удален")
    elif action == "force":
        scheduled_posts[pid]['time'] = datetime.now() - timedelta(seconds=1)
        await cb.message.delete()
        await cb.answer("🚀 Отправляю в публикацию...")

# --- ПУБЛИКАЦИЯ ---

async def publish_post(bot: Bot, data: Dict):
    text = build_post_text(data['parsed'])
    ctype, fid = data['ctype'], data['fid']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Скрипт в канале", url=WATERMARK_LINK)]])
    
    try:
        if ctype == 'photo': await bot.send_photo(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'video': await bot.send_video(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'animation': await bot.send_animation(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif ctype == 'document': await bot.send_document(CHANNEL_ID, fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else: await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=kb)
    except Exception as e:
        logger.error(f"Err pub: {e}")

# --- ЗАПУСК ---

async def scheduler(bot: Bot):
    while True:
        now = datetime.now()
        for pid in list(scheduled_posts.keys()):
            post = scheduled_posts[pid]
            if now >= post['time']:
                await publish_post(bot, post['data'])
                try:
                    await bot.send_message(post['creator_id'], f"✅ Твой пост <b>{post['data']['parsed']['game']}</b> опубликован!", parse_mode=ParseMode.HTML)
                except: pass
                del scheduled_posts[pid]
        await asyncio.sleep(5)

async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except: pass
