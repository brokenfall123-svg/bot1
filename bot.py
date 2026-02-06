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
    file = await bot.get_file(file_id)
    return await bot.download_file(file.file_path)


async def generate_image_with_seedream(prompt: str) -> bytes | None:
    """
    Seedream 4.5 через Apifree (асинхронный submit/result).[web:98]
    """
    headers = {
        "Authorization": f"Bearer {APIFREE_API_KEY}",
        "Content-Type": "application/json",
    }

    submit_payload = {
        "model": SEEDREAM_MODEL_NAME,
        "prompt": prompt,
        "seed": 8899,
        "size": "2K",
    }

    async with aiohttp.ClientSession() as session:
        # 1. Submit
        submit_url = f"{APIFREE_BASE_URL}/v1/image/submit"
        async with session.post(submit_url, headers=headers, json=submit_payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Submission failed HTTP {resp.status}: {text}")

            data = await resp.json()

        if data.get("code") != 200:
            raise RuntimeError(f"API Error: {data.get('code_msg') or data.get('error')}")

        request_id = data["resp_data"]["request_id"]

        # 2. Poll result
        result_url = f"{APIFREE_BASE_URL}/v1/image/{request_id}/result"
        max_checks = 30  # ~60 секунд при шаге 2 сек
        checks = 0

        while True:
            checks += 1
            if checks > max_checks:
                raise RuntimeError("Timeout ожидания результата от Seedream 4.5")

            await asyncio.sleep(2)

            async with session.get(result_url, headers=headers) as check_resp:
                check_data = await check_resp.json()

            if check_data.get("code") != 200:
                raise RuntimeError(f"Check failed: {check_data.get('code_msg')}")

            status = check_data["resp_data"]["status"]

            if status == "success":
                image_list = check_data["resp_data"].get("image_list") or []
                if not image_list:
                    return None

                img_url = image_list[0]

                async with session.get(img_url) as img_resp:
                    if img_resp.status != 200:
                        raise RuntimeError(
                            f"Image download failed HTTP {img_resp.status}: {await img_resp.text()}"
                        )
                    return await img_resp.read()

            if status in ("error", "failed"):
                raise RuntimeError(
                    f"Task failed: {check_data['resp_data'].get('error')}"
                )
            # иначе status: queuing / processing — продолжаем ждать


async def edit_image_with_gpt_image_1(image_bytes: bytes, prompt: str) -> bytes | None:
    """
    GPT-Image-1-Edit через OpenAI‑совместимый endpoint.[page:1][web:59]
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
