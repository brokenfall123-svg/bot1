import asyncio
import base64
import io

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,  # отправка байтов как файла
)
from aiogram.enums import ContentType

# ================== НАСТРОЙКИ ==================

TELEGRAM_BOT_TOKEN = "8559514946:AAFwULZem2V-0Rab7DIhAyHAjK8MhyFSsvM"
APIFREE_API_KEY = "sk-pjGGTiMzewfM4iiRCkfEwbXWPQ166"

APIFREE_BASE_URL = "https://api.apifree.ai"  # базовый URL Apifree[web:98]

SEEDREAM_MODEL_NAME = "bytedance/seedream-4.5"      # генерация с нуля[web:98]
GPT_IMAGE_EDIT_MODEL = "openai/gpt-image-1/edit"    # редактирование[page:1]

APIFREE_IMAGE_URL = "https://api.apifree.ai/v1/images/generations"  # для GPT-Image-1[web:59]

# ================== ИНИЦИАЛИЗАЦИЯ ==================

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

user_state: dict[int, str | None] = {}
user_lora_image: dict[int, str] = {}


# ================== КЛАВИАТУРА ==================

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="хочу img", callback_data="want_img"),
                InlineKeyboardButton(text="хочу lora", callback_data="want_lora"),
            ]
        ]
    )


# ================== /start ==================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Выбери, что хочешь сделать:",
        reply_markup=main_keyboard(),
    )


# ================== КНОПКА: ХОЧУ IMG ==================

@dp.callback_query(F.data == "want_img")
async def on_want_img(callback: CallbackQuery):
    user_id = callback.from_user.id
    user_state[user_id] = "img"
    await callback.message.answer("Напиши промт для нового изображения:")
    await callback.answer()


# ================== КНОПКА: ХОЧУ LORA ==================

@dp.callback_query(F.data == "want_lora")
async def on_want_lora(callback: CallbackQuery):
    user_id = callback.from_user.id
    user_state[user_id] = "lora_wait_image"
    user_lora_image.pop(user_id, None)

    await callback.message.answer(
        "Отправь PNG‑файл (как документ, не как фото), который нужно изменить."
    )
    await callback.answer()


# ================== ТЕКСТ ==================

@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    mode = user_state.get(user_id)
    text = message.text.strip()

    # ---------- IMG (Seedream 4.5) ----------
    if mode == "img":
        if not text:
            await message.answer("Промт пустой. Напиши, что нужно сгенерировать.")
            return

        await message.answer("Генерирую изображение (Seedream 4.5), подожди немного...")

        try:
            image_bytes = await generate_image_with_seedream(text)
        except Exception as e:
            await message.answer(f"Ошибка при генерации изображения: {e}")
            user_state[user_id] = None
            return

        if not image_bytes:
            await message.answer("Не удалось получить изображение от API.")
        else:
            try:
                file = BufferedInputFile(image_bytes, filename="seedream.png")
                await message.answer_photo(photo=file, caption="Вот твоё изображение ✅")
            except Exception as e:
                await message.answer(f"Ошибка при отправке изображения в Telegram: {e}")

        user_state[user_id] = None
        return

    # ---------- LORA EDIT (PNG уже загружен, ждём промт) ----------
    if mode == "lora_image_uploaded":
        if not text:
            await message.answer("Промт пустой. Опиши, как нужно изменить изображение.")
            return

        file_id = user_lora_image.get(user_id)
        if not file_id:
            await message.answer(
                "Изображение не найдено. Отправь PNG ещё раз и затем напиши промт.",
                reply_markup=main_keyboard(),
            )
            user_state[user_id] = None
            return

        await message.answer("Редактирую изображение (GPT‑Image‑1‑Edit), подожди немного...")

        try:
            image_bytes = await download_file_bytes(file_id)
        except Exception as e:
            await message.answer(f"Не удалось скачать изображение из Telegram: {e}")
            return

        if not image_bytes:
            await message.answer("Не удалось получить байты изображения.")
            return

        try:
            edited_bytes = await edit_image_with_gpt_image_1(image_bytes, text)
        except Exception as e:
            await message.answer(f"Ошибка при редактировании изображения: {e}")
            user_state[user_id] = None
            return

        if not edited_bytes:
            await message.answer("Не удалось получить отредактированное изображение от API.")
        else:
            try:
                file = BufferedInputFile(edited_bytes, filename="edited.png")
                await message.answer_photo(photo=file, caption="Вот отредактированное изображение ✅")
            except Exception as e:
                await message.answer(f"Ошибка при отправке отредактированного изображения: {e}")

        user_state[user_id] = None
        user_lora_image.pop(user_id, None)
        return

    # ---------- Режим не выбран ----------
    await message.answer(
        "Сначала выбери режим через кнопки:",
        reply_markup=main_keyboard(),
    )


# ================== ПРИЁМ PNG ДЛЯ LORA ==================

@dp.message(F.content_type == ContentType.DOCUMENT)
async def handle_document(message: Message):
    user_id = message.from_user.id
    mode = user_state.get(user_id)

    if mode not in ("lora_wait_image", "lora_image_uploaded"):
        await message.answer(
            "Чтобы загрузить файл для lora-редактирования, сначала нажми кнопку \"хочу lora\".",
            reply_markup=main_keyboard(),
        )
        return

    doc = message.document
    if not doc.file_name.lower().endswith(".png"):
        await message.answer("Нужен файл в формате PNG. Отправь .png документом.")
        return

    user_lora_image[user_id] = doc.file_id
    user_state[user_id] = "lora_image_uploaded"

    await message.answer("PNG получен. Теперь напиши промт, как нужно изменить изображение.")


@dp.message(F.content_type == ContentType.PHOTO)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    mode = user_state.get(user
