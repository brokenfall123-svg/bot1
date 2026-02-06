import asyncio
import io
import base64

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.enums import ContentType

# ================== НАСТРОЙКИ ==================

TELEGRAM_BOT_TOKEN = "8559514946:AAFwULZem2V-0Rab7DIhAyHAjK8MhyFSsvM"
APIFREE_API_KEY = "sk-pjGGTiMzewfM4iiRCkfEwbXWPQ166"

# Модели на Apifree
SEEDREAM_MODEL_NAME = "bytedance/seedream-4.5"          # генерация с нуля[page:1]
GPT_IMAGE_EDIT_MODEL = "openai/gpt-image-1/edit"        # редактирование картинки[page:1]

APIFREE_IMAGE_URL = "https://api.apifree.ai/v1/images/generations"  # OpenAI-совместимый images API[page:1][web:59]

# ================== ИНИЦИАЛИЗАЦИЯ ==================

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Состояния пользователей:
# img  – ожидаем текстовый промт для генерации с нуля
# lora_image_uploaded – PNG загружен, ждём промт для редактирования
user_state: dict[int, str | None] = {}
# Хранилище загруженных изображений для lora: user_id -> file_id
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
    """
    Режим генерации с нуля (Seedream 4.5).
    """
    user_id = callback.from_user.id
    user_state[user_id] = "img"
    await callback.message.answer("Напиши промт для нового изображения:")
    await callback.answer()


# ================== КНОПКА: ХОЧУ LORA (EDIT) ==================

@dp.callback_query(F.data == "want_lora")
async def on_want_lora(callback: CallbackQuery):
    """
    Режим редактирования изображения (GPT-Image-1-Edit).
    Сначала просим PNG как документ, потом промт.
    """
    user_id = callback.from_user.id
    user_state[user_id] = "lora_wait_image"
    user_lora_image.pop(user_id, None)
    await callback.message.answer(
        "Отправь PNG‑файл (как документ, не как фото), который нужно изменить."
    )
    await callback.answer()


# ================== ТЕКСТ: ПРОМТЫ ==================

@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    mode = user_state.get(user_id)
    text = message.text.strip()

    # ---- режим генерации с нуля (img) ----
    if mode == "img":
        if not text:
            await message.answer("Промт пустой. Напиши, что нужно сгенерировать.")
            return

        await message.answer("Генерирую изображение, подожди немного...")

        try:
            image_bytes = await generate_image_with_seedream(text)
        except Exception as e:
            await message.answer(f"Ошибка при генерации изображения: {e}")
            return

        if not image_bytes:
            await message.answer("Не удалось получить изображение от API.")
        else:
            file = FSInputFile(io.BytesIO(image_bytes), filename="seedream.png")
            await message.answer_photo(photo=file, caption="Вот твоё изображение ✅")

        user_state[user_id] = None
        return

    # ---- режим редактирования (lora_image_uploaded) ----
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

        await message.answer("Редактирую изображение, подожди немного...")

        # скачиваем PNG с серверов Telegram
        try:
            image_bytes = await download_file_bytes(file_id)
        except Exception as e:
            await message.answer(f"Не удалось скачать изображение из Telegram: {e}")
            return

        if not image_bytes:
            await message.answer("Не удалось получить байты изображения.")
            return

        # отправляем в GPT-Image-1-Edit
        try:
            edited_bytes = await edit_image_with_gpt_image_1(image_bytes, text)
        except Exception as e:
            await message.answer(f"Ошибка при редактировании изображения: {e}")
            return

        if not edited_bytes:
            await message.answer("Не удалось получить отредактированное изображение от API.")
        else:
            file = FSInputFile(io.BytesIO(edited_bytes), filename="edited.png")
            await message.answer_photo(photo=file, caption="Вот отредактированное изображение ✅")

        # сбрасываем состояние
        user_state[user_id] = None
        user_lora_image.pop(user_id, None)
        return

    # ---- если режим не выбран ----
    await message.answer(
        "Сначала выбери режим через кнопки:",
        reply_markup=main_keyboard(),
    )


# ================== ПРИЁМ PNG ДЛЯ LORA (EDIT) ==================

@dp.message(F.content_type == ContentType.DOCUMENT)
async def handle_document(message: Message):
    """
    Принимаем только PNG-документы для режима lora.
    """
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

    # сохраняем file_id в память и просим промт
    user_lora_image[user_id] = doc.file_id
    user_state[user_id] = "lora_image_uploaded"

    await message.answer("PNG получен. Теперь напиши промт, как нужно изменить изображение.")


@dp.message(F.content_type == ContentType.PHOTO)
async def handle_photo(message: Message):
    """
    Если прислали фото вместо документа — подсказываем, как нужно.
    """
    user_id = message.from_user.id
    mode = user_state.get(user_id)

    if mode in ("lora_wait_image", "lora_image_uploaded"):
        await message.answer(
            "Пожалуйста, пришли PNG именно как *документ*, а не как фото."
        )
    else:
        await message.answer(
            "Чтобы загрузить файл для lora-редактирования, сначала нажми кнопку \"хочу lora\".",
            reply_markup=main_keyboard(),
        )


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================

async def download_file_bytes(file_id: str) -> bytes | None:
    """
    Скачиваем файл из Telegram по file_id и возвращаем байты.
    """
    file = await bot.get_file(file_id)
    file_path = file.file_path
    return await bot.download_file(file_path)


async def generate_image_with_seedream(prompt: str) -> bytes | None:
    """
    Вызов Seedream 4.5 через Apifree (генерация с нуля, как раньше).[page:1][web:59]
    """
    payload = {
        "model": SEEDREAM_MODEL_NAME,
        "prompt": prompt,
        "size": "1024x1024",
        "response_format": "b64_json",
    }

    headers = {
        "Authorization": f"Bearer {APIFREE_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(APIFREE_IMAGE_URL, headers=headers, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Seedream HTTP {resp.status}: {text}")

            data = await resp.json()

    if "data" not in data or not data["data"]:
        return None

    b64_img = data["data"][0].get("b64_json")
    if not b64_img:
        return None

    return base64.b64decode(b64_img)


async def edit_image_with_gpt_image_1(image_bytes: bytes, prompt: str) -> bytes | None:
    """
    Вызов GPT-Image-1-Edit через Apifree.[page:1]

    По документации модель gpt-image-1-edit работает через edits endpoint, но
    на Apifree она представлена через общий OpenAI-совместимый images API:[web:59][page:1]

    Тело запроса (типичный формат):
    {
      "model": "openai/gpt-image-1/edit",
      "prompt": "сделай пиксель-арт",
      "image": "<base64 PNG>",
      "size": "1024x1024",
      "response_format": "b64_json",
      "quality": "high"
    }
    """
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": GPT_IMAGE_EDIT_MODEL,
        "prompt": prompt,
        "image": b64_image,
        "size": "1024x1024",
        "quality": "high",
        "response_format": "b64_json",
    }

    headers = {
        "Authorization": f"Bearer {APIFREE_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(APIFREE_IMAGE_URL, headers=headers, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"GPT-Image-1-Edit HTTP {resp.status}: {text}")

            data = await resp.json()

    if "data" not in data or not data["data"]:
        return None

    b64_img = data["data"][0].get("b64_json")
    if not b64_img:
        return None

    return base64.b64decode(b64_img)


# ================== ЗАПУСК ==================

async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
