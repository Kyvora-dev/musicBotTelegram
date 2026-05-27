from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

from googleapiclient.discovery import build
from ai_service import AIService
from keyboards import (
    language_keyboard,
    recommendations_keyboard,
    search_results_keyboard,
    track_actions_keyboard,
)
from lyrics_service import LyricsService
from recommendations_service import RecommendationsService
import asyncio
import yt_dlp
import glob
import json
import math
import os
import random
import re


def load_env_file(path: str = ".env"):
    """Load local development variables without overriding deployment secrets."""
    if not os.path.exists(path):
        return

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            # Server-provided environment values take precedence over local .env values.
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()

youtube = None


def validate_configuration():
    """Fail at startup if variables required for core bot operations are missing."""
    missing = [
        name
        for name, value in (("BOT_TOKEN", BOT_TOKEN), ("YOUTUBE_API_KEY", YOUTUBE_API_KEY))
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def get_youtube_client():
    """Create the YouTube Data API client lazily on the first search request."""
    global youtube
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is not configured.")
    if youtube is None:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    return youtube

song_cache    = {}       # video_id -> title
file_id_cache = {}       # video_id -> Telegram file_id for download-free resending
user_saved    = {}       # chat_id -> [{id, title}]
playlists     = {}       # chat_id -> [{id, name}]
playlist_tracks = {}     # chat_id -> {playlist_id: [{id, title}]}
legacy_saved_migrated = set()
pending_playlist_save = {}  # chat_id -> video_id awaiting a new playlist name
pending_playlist_create = set()  # chat_id awaiting a new playlist name from library
user_language = {}       # chat_id -> "uk" | "ru" | "en"
recommendation_cache = {}  # rec_id -> search query used by recommendation callbacks
recommendation_seq = 0
track_action_cache = {}    # video_id -> {"title", "artist", "query"}
recommendations_result_cache = {}  # (kind, query) -> recommendations
search_result_cache = {}  # query -> YouTube search results
result_sessions = {}  # session_id -> {chat_id, result_type, query, results, current_page}
result_session_seq = 0
current_track = {}  # chat_id -> current selected track context

DEFAULT_LANGUAGE = "uk"
LEGACY_PLAYLIST_ID = "saved"
PLAYLISTS_FILE = "playlists.json"
RESULTS_PAGE_SIZE = 5
RESULTS_FETCH_LIMIT = 25

TEXTS = {
    "uk": {
        "choose_language": "🌍 Обери мову:",
        "language_changed": "✅ Мову змінено на українську.",
        "start": (
            "🎵 Привіт! Я музичний Telegram-бот.\n"
            "Я допоможу знайти музику, схожі треки, виконавців, "
            "тексти пісень і плейлісти."
        ),
        "saved_empty": "❌ Немає збережених пісень.",
        "saved_title": "❤️ Збережені пісні:",
        "checking": "⏳ Перевіряю трек...",
        "too_long": "❌ Трек задовгий ({minutes} хв). Telegram підтримує до {max_minutes} хв.",
        "info_error": "❌ Не вдалося отримати інформацію про трек.",
        "downloading": "⬇️ Завантажую: {title}",
        "file_missing": "❌ Файл не знайдено після завантаження.",
        "file_empty": "❌ Завантажений файл порожній.",
        "file_too_large": "❌ Файл задто великий ({size_mb:.1f} MB). Telegram приймає до {max_mb} MB.",
        "sending": "📤 Надсилаю...",
        "error": "❌ Помилка: {error}",
        "not_found": "🔍 Нічого не знайдено.",
        "choose_track": "🎧 Обери трек:",
        "track_card": "🎵 {title}\n👤 {artist}",
        "selected_track_info": "🎵 {title}\n👤 {artist}\n⏱ {duration}",
        "unknown_artist": "Невідомий артист",
        "similar_tracks_button": "🎧 Схожі треки",
        "similar_artists_button": "👤 Схожі артисти",
        "lyrics_button": "📝 Текст пісні",
        "similar_tracks_title": "🎧 Схожі треки:",
        "similar_artists_title": "👤 Схожі артисти:",
        "lyrics_unavailable": "Lyrics are not available for this track",
        "lyrics_title": "📝 Текст пісні:",
        "lyrics_searching": "📝 Шукаю текст пісні...",
        "actions_track_expired": "ℹ️ Дані треку застаріли. Надішли запит ще раз.",
        "saved_ok": "❤️ Збережено!",
        "already_saved": "Вже збережено.",
        "choose_playlist": "💾 Обери плейліст:",
        "no_playlists_for_save": "У тебе ще немає плейлістів.",
        "default_playlist": "❤️ Збережені пісні",
        "playlist_tracks_title": "🎵 Плейліст: {name}",
        "already_in_playlist": "Цей трек вже є у плейлісті «{name}».",
        "saved_to_playlist": "Збережено у плейліст «{name}».",
        "create_playlist": "➕ Створити плейліст",
        "new_playlist": "➕ Новий плейліст",
        "cancel": "❌ Скасувати",
        "save_cancelled": "Збереження скасовано.",
        "playlist_name_prompt": "Напиши назву нового плейліста:",
        "playlist_name_empty": "Назва плейліста не може бути порожньою. Спробуй ще раз:",
        "playlist_name_too_long": "Назва плейліста має містити не більше 60 символів. Спробуй ще раз:",
        "playlist_exists": "Плейліст «{name}» вже існує. Обери іншу назву:",
        "main_menu": "🏠 Обери дію через кнопку Menu біля поля вводу.",
        "search_button": "🎵 Пошук пісні",
        "my_playlists": "📂 Мої плейлісти",
        "music_picker": "🎧 Підібрати музику",
        "language_button": "🌍 Мова",
        "help_button": "❓ Допомога",
        "search_prompt": "🎵 Напиши назву пісні або виконавця, якого хочеш знайти.",
        "help_text": (
            "❓ Допомога\n\n"
            "🎵 Що вміє бот:\n"
            "• знаходити пісні\n"
            "• підбирати музику за настроєм через AI/music picker\n"
            "• шукати схожі треки та артистів\n"
            "• показувати текст пісень\n"
            "• зберігати музику у плейлісти\n"
            "• працювати українською, російською та англійською\n\n"
            "📌 Команди:\n"
            "/start — головне меню\n"
            "/search — знайти пісню\n"
            "/playlists — мої плейлісти\n"
            "/mood — музика за настроєм\n"
            "/language — змінити мову\n"
            "/help — допомога\n\n"
            "💡 Як користуватись:\n"
            "• Просто напиши назву пісні або виконавця\n"
            "• Або відкрий «🎧 Підібрати музику»\n"
            "• Після вибору треку доступні:\n"
            "  - lyrics\n"
            "  - схожі треки\n"
            "  - схожі артисти\n"
            "  - збереження у плейліст"
        ),
        "music_picker_intro": "🎧 Обери настрій або активність, і я підберу треки для тебе.",
        "picker_workout": "🏋️ Для тренування",
        "picker_reading": "📚 Для читання / роботи",
        "picker_evening": "🌙 Для вечора",
        "picker_sad": "😔 Сумний настрій",
        "picker_road": "🚗 Для дороги",
        "picker_popular": "🔥 Популярне",
        "picker_surprise": "🎲 Здивуй мене",
        "playlists_title": "📂 Мої плейлісти:",
        "playlists_empty": "У тебе ще немає плейлістів.",
        "playlist_created": "Плейліст «{name}» створено.",
        "playlist_empty": "У плейлісті «{name}» ще немає пісень.",
        "edit_playlist": "✏️ Редагувати плейліст",
        "delete_playlist": "🗑 Видалити плейліст",
        "back": "⬅️ Назад",
        "edit_playlist_title": "✏️ Редагування: {name}",
        "remove_track": "🗑 {title}",
        "confirm_delete_playlist": "Видалити плейліст «{name}» разом з усіма треками?",
        "confirm_remove_track": "Видалити «{title}» з плейліста «{name}»?",
        "confirm": "✅ Так, видалити",
        "playlist_deleted": "Плейліст «{name}» видалено.",
        "track_removed": "Трек «{title}» видалено з плейліста «{name}».",
        "previous": "⬅️ Назад",
        "next": "Далі ➡️",
        "save": "💾 Зберегти",
        "saved_button": "✅ Збережено",
        "recommendations_title": "✨ Схожі треки та артисти:",
        "recommendations_fallback": "ℹ️ Рекомендації зараз недоступні. Спробуй пізніше.",
        "recommendations_not_found": "ℹ️ Не знайшов схожих рекомендацій для цього запиту.",
        "recommendation_opening": "🔎 Шукаю цю рекомендацію на YouTube...",
        "recommendation_expired": "ℹ️ Ця рекомендація застаріла. Надішли запит ще раз.",
        "intent_tracks_title": "✨ Треки за твоїм настроєм:",
    },
    "ru": {
        "choose_language": "🌍 Выбери язык:",
        "language_changed": "✅ Язык изменён на русский.",
        "start": (
            "🎵 Привет! Я музыкальный Telegram-бот.\n"
            "Я помогу найти музыку, похожие треки, исполнителей, "
            "тексты песен и плейлисты."
        ),
        "saved_empty": "❌ Нет сохранённых песен.",
        "saved_title": "❤️ Сохранённые песни:",
        "checking": "⏳ Проверяю трек...",
        "too_long": "❌ Трек слишком длинный ({minutes} мин). Telegram поддерживает до {max_minutes} мин.",
        "info_error": "❌ Не удалось получить информацию о треке.",
        "downloading": "⬇️ Загружаю: {title}",
        "file_missing": "❌ Файл не найден после загрузки.",
        "file_empty": "❌ Загруженный файл пустой.",
        "file_too_large": "❌ Файл слишком большой ({size_mb:.1f} MB). Telegram принимает до {max_mb} MB.",
        "sending": "📤 Отправляю...",
        "error": "❌ Ошибка: {error}",
        "not_found": "🔍 Ничего не найдено.",
        "choose_track": "🎧 Выбери трек:",
        "track_card": "🎵 {title}\n👤 {artist}",
        "selected_track_info": "🎵 {title}\n👤 {artist}\n⏱ {duration}",
        "unknown_artist": "Неизвестный артист",
        "similar_tracks_button": "🎧 Похожие треки",
        "similar_artists_button": "👤 Похожие артисты",
        "lyrics_button": "📝 Текст песни",
        "similar_tracks_title": "🎧 Похожие треки:",
        "similar_artists_title": "👤 Похожие артисты:",
        "lyrics_unavailable": "Lyrics are not available for this track",
        "lyrics_title": "📝 Текст песни:",
        "lyrics_searching": "📝 Ищу текст песни...",
        "actions_track_expired": "ℹ️ Данные трека устарели. Отправь запрос ещё раз.",
        "saved_ok": "❤️ Сохранено!",
        "already_saved": "Уже сохранено.",
        "choose_playlist": "💾 Выбери плейлист:",
        "no_playlists_for_save": "У тебя ещё нет плейлистов.",
        "default_playlist": "❤️ Сохранённые песни",
        "playlist_tracks_title": "🎵 Плейлист: {name}",
        "already_in_playlist": "Этот трек уже есть в плейлисте «{name}».",
        "saved_to_playlist": "Сохранено в плейлист «{name}».",
        "create_playlist": "➕ Создать плейлист",
        "new_playlist": "➕ Новый плейлист",
        "cancel": "❌ Отменить",
        "save_cancelled": "Сохранение отменено.",
        "playlist_name_prompt": "Напиши название нового плейлиста:",
        "playlist_name_empty": "Название плейлиста не может быть пустым. Попробуй ещё раз:",
        "playlist_name_too_long": "Название плейлиста должно содержать не больше 60 символов. Попробуй ещё раз:",
        "playlist_exists": "Плейлист «{name}» уже существует. Выбери другое название:",
        "main_menu": "🏠 Выбери действие через кнопку Menu возле поля ввода.",
        "search_button": "🎵 Поиск песни",
        "my_playlists": "📂 Мои плейлисты",
        "music_picker": "🎧 Подобрать музыку",
        "language_button": "🌍 Язык",
        "help_button": "❓ Помощь",
        "search_prompt": "🎵 Напиши название песни или исполнителя, которого хочешь найти.",
        "help_text": (
            "❓ Помощь\n\n"
            "🎵 Что умеет бот:\n"
            "• находить песни\n"
            "• подбирать музыку по настроению через AI/music picker\n"
            "• искать похожие треки и исполнителей\n"
            "• показывать тексты песен\n"
            "• сохранять музыку в плейлисты\n"
            "• работать на русском, украинском и английском\n\n"
            "📌 Команды:\n"
            "/start — главное меню\n"
            "/search — найти песню\n"
            "/playlists — мои плейлисты\n"
            "/mood — музыка по настроению\n"
            "/language — изменить язык\n"
            "/help — помощь\n\n"
            "💡 Как пользоваться:\n"
            "• Просто напиши название песни или исполнителя\n"
            "• Или открой «🎧 Подобрать музыку»\n"
            "• После выбора трека доступны:\n"
            "  - lyrics\n"
            "  - похожие треки\n"
            "  - похожие исполнители\n"
            "  - сохранение в плейлист"
        ),
        "music_picker_intro": "🎧 Выбери настроение или занятие, и я подберу треки для тебя.",
        "picker_workout": "🏋️ Для тренировки",
        "picker_reading": "📚 Для чтения / работы",
        "picker_evening": "🌙 Для вечера",
        "picker_sad": "😔 Грустное настроение",
        "picker_road": "🚗 Для дороги",
        "picker_popular": "🔥 Популярное",
        "picker_surprise": "🎲 Удиви меня",
        "playlists_title": "📂 Мои плейлисты:",
        "playlists_empty": "У тебя ещё нет плейлистов.",
        "playlist_created": "Плейлист «{name}» создан.",
        "playlist_empty": "В плейлисте «{name}» пока нет песен.",
        "edit_playlist": "✏️ Редактировать плейлист",
        "delete_playlist": "🗑 Удалить плейлист",
        "back": "⬅️ Назад",
        "edit_playlist_title": "✏️ Редактирование: {name}",
        "remove_track": "🗑 {title}",
        "confirm_delete_playlist": "Удалить плейлист «{name}» вместе со всеми треками?",
        "confirm_remove_track": "Удалить «{title}» из плейлиста «{name}»?",
        "confirm": "✅ Да, удалить",
        "playlist_deleted": "Плейлист «{name}» удалён.",
        "track_removed": "Трек «{title}» удалён из плейлиста «{name}».",
        "previous": "⬅️ Назад",
        "next": "Далее ➡️",
        "save": "💾 Сохранить",
        "saved_button": "✅ Сохранено",
        "recommendations_title": "✨ Похожие треки и артисты:",
        "recommendations_fallback": "ℹ️ Рекомендации сейчас недоступны. Попробуй позже.",
        "recommendations_not_found": "ℹ️ Не нашёл похожих рекомендаций для этого запроса.",
        "recommendation_opening": "🔎 Ищу эту рекомендацию на YouTube...",
        "recommendation_expired": "ℹ️ Эта рекомендация устарела. Отправь запрос ещё раз.",
        "intent_tracks_title": "✨ Треки под твоё настроение:",
    },
    "en": {
        "choose_language": "🌍 Choose language:",
        "language_changed": "✅ Language changed to English.",
        "start": (
            "🎵 Hello! I am a music Telegram bot.\n"
            "I can help you find music, similar tracks, artists, "
            "song lyrics, and playlists."
        ),
        "saved_empty": "❌ No saved songs yet.",
        "saved_title": "❤️ Saved songs:",
        "checking": "⏳ Checking track...",
        "too_long": "❌ This track is too long ({minutes} min). Telegram supports up to {max_minutes} min.",
        "info_error": "❌ Could not get track information.",
        "downloading": "⬇️ Downloading: {title}",
        "file_missing": "❌ File was not found after downloading.",
        "file_empty": "❌ Downloaded file is empty.",
        "file_too_large": "❌ File is too large ({size_mb:.1f} MB). Telegram accepts up to {max_mb} MB.",
        "sending": "📤 Sending...",
        "error": "❌ Error: {error}",
        "not_found": "🔍 Nothing found.",
        "choose_track": "🎧 Choose a track:",
        "track_card": "🎵 {title}\n👤 {artist}",
        "selected_track_info": "🎵 {title}\n👤 {artist}\n⏱ {duration}",
        "unknown_artist": "Unknown artist",
        "similar_tracks_button": "🎧 Similar tracks",
        "similar_artists_button": "👤 Similar artists",
        "lyrics_button": "📝 Lyrics",
        "similar_tracks_title": "🎧 Similar tracks:",
        "similar_artists_title": "👤 Similar artists:",
        "lyrics_unavailable": "Lyrics are not available for this track",
        "lyrics_title": "📝 Lyrics:",
        "lyrics_searching": "📝 Searching lyrics...",
        "actions_track_expired": "ℹ️ Track data expired. Send the query again.",
        "saved_ok": "❤️ Saved!",
        "already_saved": "Already saved.",
        "choose_playlist": "💾 Choose a playlist:",
        "no_playlists_for_save": "You do not have any playlists yet.",
        "default_playlist": "❤️ Saved songs",
        "playlist_tracks_title": "🎵 Playlist: {name}",
        "already_in_playlist": "This track is already in \"{name}\".",
        "saved_to_playlist": "Saved to \"{name}\".",
        "create_playlist": "➕ Create playlist",
        "new_playlist": "➕ New playlist",
        "cancel": "❌ Cancel",
        "save_cancelled": "Saving cancelled.",
        "playlist_name_prompt": "Send a name for the new playlist:",
        "playlist_name_empty": "The playlist name cannot be empty. Try again:",
        "playlist_name_too_long": "Playlist names must be no more than 60 characters. Try again:",
        "playlist_exists": "A playlist named \"{name}\" already exists. Choose another name:",
        "main_menu": "🏠 Choose an action from the Menu button next to the input field.",
        "search_button": "🎵 Search for a song",
        "my_playlists": "📂 My playlists",
        "music_picker": "🎧 Pick music",
        "language_button": "🌍 Language",
        "help_button": "❓ Help",
        "search_prompt": "🎵 Send the song title or artist you want to find.",
        "help_text": (
            "❓ Help\n\n"
            "🎵 What this bot can do:\n"
            "• find songs\n"
            "• pick music for your mood with the AI/music picker\n"
            "• find similar tracks and artists\n"
            "• show song lyrics\n"
            "• save music to playlists\n"
            "• work in English, Ukrainian, and Russian\n\n"
            "📌 Commands:\n"
            "/start — main menu\n"
            "/search — find a song\n"
            "/playlists — my playlists\n"
            "/mood — music for your mood\n"
            "/language — change language\n"
            "/help — help\n\n"
            "💡 How to use it:\n"
            "• Simply send a song title or artist name\n"
            "• Or open “🎧 Pick music”\n"
            "• After choosing a track, you can use:\n"
            "  - lyrics\n"
            "  - similar tracks\n"
            "  - similar artists\n"
            "  - save to playlist"
        ),
        "music_picker_intro": "🎧 Choose a mood or activity and I will find tracks for you.",
        "picker_workout": "🏋️ For a workout",
        "picker_reading": "📚 For reading / work",
        "picker_evening": "🌙 For the evening",
        "picker_sad": "😔 Sad mood",
        "picker_road": "🚗 For a drive",
        "picker_popular": "🔥 Popular",
        "picker_surprise": "🎲 Surprise me",
        "playlists_title": "📂 My playlists:",
        "playlists_empty": "You do not have any playlists yet.",
        "playlist_created": "Playlist \"{name}\" created.",
        "playlist_empty": "There are no songs in \"{name}\" yet.",
        "edit_playlist": "✏️ Edit playlist",
        "delete_playlist": "🗑 Delete playlist",
        "back": "⬅️ Back",
        "edit_playlist_title": "✏️ Editing: {name}",
        "remove_track": "🗑 {title}",
        "confirm_delete_playlist": "Delete playlist \"{name}\" and all its tracks?",
        "confirm_remove_track": "Remove \"{title}\" from playlist \"{name}\"?",
        "confirm": "✅ Yes, delete",
        "playlist_deleted": "Playlist \"{name}\" deleted.",
        "track_removed": "Removed \"{title}\" from playlist \"{name}\".",
        "previous": "⬅️ Previous",
        "next": "Next ➡️",
        "save": "💾 Save",
        "saved_button": "✅ Saved",
        "recommendations_title": "✨ Similar tracks and artists:",
        "recommendations_fallback": "ℹ️ Recommendations are unavailable right now. Try again later.",
        "recommendations_not_found": "ℹ️ I could not find similar recommendations for this query.",
        "recommendation_opening": "🔎 Searching this recommendation on YouTube...",
        "recommendation_expired": "ℹ️ This recommendation expired. Send the query again.",
        "intent_tracks_title": "✨ Tracks for your mood:",
    },
}


def get_lang(chat_id) -> str:
    """Return a user's saved interface language or the default localization."""
    return user_language.get(str(chat_id), DEFAULT_LANGUAGE)


def t(chat_id, key: str, **kwargs) -> str:
    """Resolve and format a localized UI string for the current chat."""
    text = TEXTS.get(get_lang(chat_id), TEXTS[DEFAULT_LANGUAGE]).get(key, key)
    return text.format(**kwargs) if kwargs else text


def action_texts(chat_id) -> dict:
    return {
        "similar_tracks": t(chat_id, "similar_tracks_button"),
        "similar_artists": t(chat_id, "similar_artists_button"),
        "lyrics": t(chat_id, "lyrics_button"),
        "save": t(chat_id, "save"),
        "saved": t(chat_id, "saved_button"),
    }


def main_menu_keyboard(chat_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(chat_id, "search_button"), callback_data="search_menu")],
        [InlineKeyboardButton(t(chat_id, "my_playlists"), callback_data="my_playlists")],
        [InlineKeyboardButton(t(chat_id, "music_picker"), callback_data="music_picker")],
        [InlineKeyboardButton(t(chat_id, "language_button"), callback_data="language_menu")],
        [InlineKeyboardButton(t(chat_id, "help_button"), callback_data="help_menu")],
    ])


def music_picker_keyboard(chat_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(chat_id, "picker_workout"), callback_data="pick_music:workout")],
        [InlineKeyboardButton(t(chat_id, "picker_reading"), callback_data="pick_music:reading")],
        [InlineKeyboardButton(t(chat_id, "picker_evening"), callback_data="pick_music:evening")],
        [InlineKeyboardButton(t(chat_id, "picker_sad"), callback_data="pick_music:sad")],
        [InlineKeyboardButton(t(chat_id, "picker_road"), callback_data="pick_music:road")],
        [InlineKeyboardButton(t(chat_id, "picker_popular"), callback_data="pick_music:popular")],
        [InlineKeyboardButton(t(chat_id, "picker_surprise"), callback_data="pick_music:surprise")],
        [InlineKeyboardButton(t(chat_id, "back"), callback_data="main_menu")],
    ])


def music_picker_tags(preset: str) -> list[str] | None:
    # Presets become Last.fm tags, sharing the same recommendation path as parsed moods.
    presets = {
        "workout": ["workout", "energetic"],
        "reading": ["ambient", "instrumental"],
        "evening": ["chillout", "downtempo"],
        "sad": ["sad", "melancholic"],
        "road": ["road trip", "indie rock"],
        "popular": ["pop", "dance"],
    }
    if preset == "surprise":
        return random.choice(list(presets.values()))
    return presets.get(preset)


def get_playlist(chat_id: str, playlist_id: str) -> dict | None:
    return next(
        (item for item in playlists.get(chat_id, []) if item["id"] == playlist_id),
        None,
    )


def playlists_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(item["name"][:45], callback_data=f"open_playlist:{item['id']}")]
        for item in playlists.get(chat_id, [])
    ]
    buttons.append([
        InlineKeyboardButton(t(chat_id, "create_playlist"), callback_data="new_playlist_menu")
    ])
    buttons.append([
        InlineKeyboardButton(t(chat_id, "back"), callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(buttons)


def playlist_view_keyboard(chat_id: str, playlist_id: str, tracks: list) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f"🎵 {track['title'][:45]}", callback_data=f"play:{track['id']}")]
        for track in tracks
    ]
    buttons.extend([
        [InlineKeyboardButton(t(chat_id, "edit_playlist"), callback_data=f"edit_playlist:{playlist_id}")],
        [InlineKeyboardButton(t(chat_id, "delete_playlist"), callback_data=f"confirm_delete_playlist:{playlist_id}")],
        [InlineKeyboardButton(t(chat_id, "back"), callback_data="my_playlists")],
    ])
    return InlineKeyboardMarkup(buttons)


def playlist_edit_keyboard(chat_id: str, playlist_id: str, tracks: list) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                t(chat_id, "remove_track", title=track["title"][:40]),
                callback_data=f"confirm_remove_track:{playlist_id}:{track['id']}",
            )
        ]
        for track in tracks
    ]
    buttons.append([
        InlineKeyboardButton(t(chat_id, "back"), callback_data=f"open_playlist:{playlist_id}")
    ])
    return InlineKeyboardMarkup(buttons)


def create_result_session(
    chat_id: str,
    result_type: str,
    query: str,
    results: list,
    title_key: str,
) -> str:
    global result_session_seq, recommendation_seq

    # Callback data carries only this small ID; full page state remains in memory.
    session_id = str(result_session_seq)
    result_session_seq += 1
    state = {
        "chat_id": chat_id,
        "result_type": result_type,
        "query": query,
        "results": results,
        "title_key": title_key,
        "current_page": 0,
    }
    if result_type != "search":
        state["recommendation_start"] = recommendation_seq
        recommendation_seq += len(results)
    result_sessions[session_id] = state
    return session_id


def result_page_keyboard(chat_id: str, session_id: str, state: dict) -> InlineKeyboardMarkup:
    # Search and recommendation pages share navigation while keeping different callbacks.
    page = state["current_page"]
    results = state["results"]
    total_pages = max(1, math.ceil(len(results) / RESULTS_PAGE_SIZE))
    start = page * RESULTS_PAGE_SIZE
    page_results = results[start:start + RESULTS_PAGE_SIZE]
    options = {
        "page": page,
        "total_pages": total_pages,
        "callback_prefix": f"results_page:{session_id}",
        "previous_text": t(chat_id, "previous"),
        "next_text": t(chat_id, "next"),
    }
    if state["result_type"] == "search":
        return search_results_keyboard(page_results, **options)
    return recommendations_keyboard(
        page_results,
        recommendation_cache,
        start_index=state["recommendation_start"] + start,
        **options,
    )


def format_duration(seconds: int) -> str:
    if not seconds:
        return "--:--"

    minutes, sec = divmod(seconds, 60)
    return f"{minutes}:{sec:02d}"


def clean_lyrics_title(title: str) -> str:
    cleaned = re.sub(
        r"[\[(][^)\]]*(official|video|audio|lyric|remix|live|feat\.?|ft\.?)[^)\]]*[\])]",
        "",
        title,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(official\s*(music\s*)?video|official\s*audio|lyrics?|remix|live)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(feat\.?|ft\.)\s+.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*[-|]\s*$", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def clean_lyrics_artist(artist: str, title: str) -> str:
    title_artist = re.split(r"\s+[-–—]\s+", title, maxsplit=1)[0].strip()
    if title_artist and title_artist.lower() != title.lower():
        return title_artist

    cleaned = re.sub(r"\s*-\s*topic$", "", artist, flags=re.IGNORECASE)
    cleaned = re.sub(r"vevo$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def build_track_context(chat_id: str, video_id: str, title: str, artist: str, duration: int = 0) -> dict:
    artist = artist or t(chat_id, "unknown_artist")
    lyrics_artist = clean_lyrics_artist(artist, title)
    lyrics_title = clean_lyrics_title(title)
    lyrics_title = re.sub(
        rf"^\s*{re.escape(lyrics_artist)}\s*[-–—]\s*",
        "",
        lyrics_title,
        flags=re.IGNORECASE,
    ).strip()
    return {
        "id": video_id,
        "title": title,
        "artist": artist,
        "duration": duration,
        "query": f"{artist} {title}".strip(),
        "lyrics_artist": lyrics_artist,
        "lyrics_title": lyrics_title,
    }


def get_action_track(chat_id: str, video_id: str) -> dict | None:
    selected = current_track.get(chat_id)
    if selected and selected.get("id") == video_id:
        return selected
    return track_action_cache.get(video_id)


# ─── Upload limits ──────────────────────────────────────────────────────────
MAX_DURATION_SEC = 10 * 60   # Avoid sending excessively long audio files.
MAX_FILE_BYTES   = 48 * 1024 * 1024  # 48 MB (Telegram bot limit = 50 MB)

DEBUG = True
lyrics_service = LyricsService()
recommendations_service = RecommendationsService()
ai_service = AIService()

def debug(*args):
    if DEBUG:
        print("🔍", *args)

# ─── Local persistence ──────────────────────────────────────────────────────
def load_saved():
    global user_saved
    try:
        with open("saved.json", "r") as f:
            user_saved = json.load(f)
    except Exception:
        user_saved = {}

def save_saved():
    with open("saved.json", "w") as f:
        json.dump(user_saved, f, indent=2, ensure_ascii=False)


def save_playlists():
    # Metadata and track membership are persisted separately to keep playlist edits simple.
    data = {
        "playlists": playlists,
        "playlist_tracks": playlist_tracks,
        "legacy_saved_migrated": sorted(legacy_saved_migrated),
    }
    with open(PLAYLISTS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def ensure_legacy_playlist(chat_id: str) -> bool:
    changed = False
    user_playlists = playlists.setdefault(chat_id, [])
    if not any(item["id"] == LEGACY_PLAYLIST_ID for item in user_playlists):
        user_playlists.insert(
            0,
            {"id": LEGACY_PLAYLIST_ID, "name": t(chat_id, "default_playlist")},
        )
        changed = True

    user_tracks = playlist_tracks.setdefault(chat_id, {})
    if LEGACY_PLAYLIST_ID not in user_tracks:
        user_tracks[LEGACY_PLAYLIST_ID] = []
        changed = True
    return changed


def create_playlist(chat_id: str, name: str) -> dict:
    used_ids = {item["id"] for item in playlists.get(chat_id, [])}
    index = len(used_ids) + 1
    playlist_id = f"playlist_{index}"
    while playlist_id in used_ids:
        index += 1
        playlist_id = f"playlist_{index}"

    playlist = {"id": playlist_id, "name": name}
    playlists.setdefault(chat_id, []).append(playlist)
    playlist_tracks.setdefault(chat_id, {})[playlist_id] = []
    return playlist


def add_track_to_playlist(chat_id: str, playlist_id: str, video_id: str) -> bool:
    tracks = playlist_tracks.setdefault(chat_id, {}).setdefault(playlist_id, [])
    if any(item["id"] == video_id for item in tracks):
        return False

    tracks.append({"id": video_id, "title": song_cache.get(video_id, "Song")})
    save_playlists()
    return True


def load_playlists():
    global playlists, playlist_tracks, legacy_saved_migrated
    try:
        with open(PLAYLISTS_FILE, "r") as f:
            data = json.load(f)
        playlists = data.get("playlists", {})
        playlist_tracks = data.get("playlist_tracks", {})
        legacy_saved_migrated = set(data.get("legacy_saved_migrated", []))
    except Exception:
        playlists = {}
        playlist_tracks = {}
        legacy_saved_migrated = set()

    # Import the older saved-song list once so existing users retain their library.
    changed = False
    for chat_id, songs in user_saved.items():
        if chat_id in legacy_saved_migrated:
            continue
        if songs:
            changed = ensure_legacy_playlist(chat_id) or changed
            imported = playlist_tracks[chat_id][LEGACY_PLAYLIST_ID]
            imported_ids = {song["id"] for song in imported}
            for song in songs:
                if song["id"] not in imported_ids:
                    imported.append({"id": song["id"], "title": song["title"]})
                    imported_ids.add(song["id"])
                    changed = True
        legacy_saved_migrated.add(chat_id)
        changed = True

    if changed:
        save_playlists()


def load_languages():
    """Load user language preferences stored between bot restarts."""
    global user_language
    try:
        with open("languages.json", "r") as f:
            user_language = json.load(f)
    except Exception:
        user_language = {}


def save_languages():
    """Persist user language preferences for future conversations."""
    with open("languages.json", "w") as f:
        json.dump(user_language, f, indent=2, ensure_ascii=False)

# ─── YouTube Search ─────────────────────────────────────────────────────────
def search_youtube(query: str) -> list[dict]:
    """Return lightweight YouTube results; audio is fetched only after selection."""
    res = get_youtube_client().search().list(
        q=f"{query} music",
        part="snippet",
        maxResults=RESULTS_FETCH_LIMIT,
        type="video"
    ).execute()

    results = []
    for item in res.get("items", []):
        vid   = item.get("id", {}).get("videoId")
        title = item.get("snippet", {}).get("title", "Song")
        artist = item.get("snippet", {}).get("channelTitle", "")
        if vid:
            results.append({"id": vid, "title": title, "artist": artist})
    return results

# ─── Track download flow ────────────────────────────────────────────────────
def check_duration(video_id: str, chat_id) -> tuple[bool, int, str]:
    """Validate duration from metadata before downloading any audio bytes."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "skip_download": True,
        # Metadata is sufficient for the pre-download duration check.
        "format": "bestaudio/best",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        duration = info.get("duration") or 0
        title    = info.get("title", "Song")

        if duration > MAX_DURATION_SEC:
            mins = duration // 60
            return False, duration, t(
                chat_id,
                "too_long",
                minutes=mins,
                max_minutes=MAX_DURATION_SEC // 60,
            )

        return True, duration, title

    except Exception as e:
        debug("check_duration error:", e)
        return False, 0, t(chat_id, "info_error")

def find_downloaded_file(video_id: str) -> str | None:
    """Locate the temporary output because yt-dlp chooses the audio extension."""
    patterns = [
        f"/tmp/{video_id}.*",
        f"/tmp/{video_id}",
    ]
    for pat in patterns:
        files = glob.glob(pat)
        if files:
            return files[0]
    return None


def download_audio(video_id: str) -> str | None:
    """Download one audio stream into temporary storage for Telegram upload."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": f"/tmp/{video_id}.%(ext)s",
        "quiet": True,
        "noplaylist": True,
        "postprocessors": [],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)

    return find_downloaded_file(video_id)

# ─── Telegram player delivery ───────────────────────────────────────────────
async def send_controls(chat_id, context, video_id: str, title: str):
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎧 {title}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(t(chat_id, "save"), callback_data=f"save:{video_id}"),
            ]
        ])
    )

async def send_player(chat_id, context, video_id: str, title: str, show_controls: bool = True):
    """Send cached Telegram audio or download, validate, upload, and cache a track."""

    # A Telegram file_id avoids repeated yt-dlp downloads for tracks already sent once.
    if video_id in file_id_cache:
        debug(f"Cache hit for {video_id}, sending via file_id")
        cached = file_id_cache[video_id]
        await context.bot.send_audio(
            chat_id=chat_id,
            audio=cached["file_id"],
            title=cached["title"],
            duration=cached["duration"],
        )
        if show_controls:
            await send_controls(chat_id, context, video_id, cached["title"])
        return {"title": cached["title"], "duration": cached["duration"]}

    # Read metadata first so over-limit audio is rejected before downloading.
    status_msg = await context.bot.send_message(chat_id, t(chat_id, "checking"))

    ok, duration, info_text = await asyncio.to_thread(check_duration, video_id, chat_id)
    if not ok:
        await context.bot.edit_message_text(info_text, chat_id, status_msg.message_id)
        return

    # Prefer the metadata title when search returned only a generic fallback.
    if title == "Song":
        title = info_text
    song_cache[video_id] = title

    await context.bot.edit_message_text(t(chat_id, "downloading", title=title), chat_id, status_msg.message_id)

    file_path = None

    try:
        file_path = await asyncio.to_thread(download_audio, video_id)

        # Validate the temporary file before passing it to Telegram.
        if not file_path or not os.path.exists(file_path):
            await context.bot.edit_message_text(t(chat_id, "file_missing"), chat_id, status_msg.message_id)
            return

        file_size = os.path.getsize(file_path)

        if file_size == 0:
            await context.bot.edit_message_text(t(chat_id, "file_empty"), chat_id, status_msg.message_id)
            os.remove(file_path)
            return

        # Stay below Telegram's upload limit.
        if file_size > MAX_FILE_BYTES:
            size_mb = file_size / 1024 / 1024
            await context.bot.edit_message_text(
                t(chat_id, "file_too_large", size_mb=size_mb, max_mb=MAX_FILE_BYTES // 1024 // 1024),
                chat_id, status_msg.message_id
            )
            os.remove(file_path)
            return

        # Upload only audio files that passed the local validation checks.
        await context.bot.edit_message_text(t(chat_id, "sending"), chat_id, status_msg.message_id)

        with open(file_path, "rb") as audio_file:
            audio_msg = await context.bot.send_audio(
                chat_id=chat_id,
                audio=audio_file,
                title=title,
                duration=duration,
            )

        # Telegram file IDs permit future sends without another YouTube download.
        file_id_cache[video_id] = {
            "file_id":  audio_msg.audio.file_id,
            "title":    title,
            "duration": duration,
        }
        debug(f"Saved file_id for {video_id}: {audio_msg.audio.file_id[:20]}...")

    except Exception as e:
        debug("send_player error:", e)
        await context.bot.edit_message_text(t(chat_id, "error", error=e), chat_id, status_msg.message_id)
        return

    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

    # Remove transient progress output after the upload attempt.
    try:
        await context.bot.delete_message(chat_id, status_msg.message_id)
    except Exception:
        pass

    if show_controls:
        await send_controls(chat_id, context, video_id, title)

    return {"title": title, "duration": duration}

# ─── /start ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    pending_playlist_save.pop(chat_id, None)
    pending_playlist_create.discard(chat_id)
    await update.message.reply_text(
        t(chat_id, "start"),
        reply_markup=main_menu_keyboard(chat_id),
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    pending_playlist_save.pop(chat_id, None)
    pending_playlist_create.discard(chat_id)
    await update.message.reply_text(t(chat_id, "search_prompt"))


async def playlists_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    pending_playlist_save.pop(chat_id, None)
    pending_playlist_create.discard(chat_id)
    await update.message.reply_text(
        t(chat_id, "playlists_title") if playlists.get(chat_id) else t(chat_id, "playlists_empty"),
        reply_markup=playlists_keyboard(chat_id),
    )


async def mood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    pending_playlist_save.pop(chat_id, None)
    pending_playlist_create.discard(chat_id)
    await update.message.reply_text(
        t(chat_id, "music_picker_intro"),
        reply_markup=music_picker_keyboard(chat_id),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    pending_playlist_save.pop(chat_id, None)
    pending_playlist_create.discard(chat_id)
    await update.message.reply_text(t(chat_id, "help_text"))

# ─── /language ──────────────────────────────────────────────────────────────
async def language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text(
        t(chat_id, "choose_language"),
        reply_markup=language_keyboard()
    )

# ─── /saved ─────────────────────────────────────────────────────────────────
async def saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_playlists = playlists.get(chat_id, [])
    has_tracks = any(playlist_tracks.get(chat_id, {}).get(item["id"], []) for item in user_playlists)

    if not has_tracks:
        await update.message.reply_text(t(chat_id, "saved_empty"))
        return

    buttons = [
        [InlineKeyboardButton(item["name"][:45], callback_data=f"open_playlist:{item['id']}")]
        for item in user_playlists
    ]
    await update.message.reply_text(
        t(chat_id, "saved_title"),
        reply_markup=InlineKeyboardMarkup(buttons)
    )


def playlist_detail_text(chat_id: str, playlist: dict, tracks: list) -> str:
    if not tracks:
        return t(chat_id, "playlist_empty", name=playlist["name"])
    return t(chat_id, "playlist_tracks_title", name=playlist["name"])


async def send_recommendation_results(chat_id, context, title_key: str, cache_key: tuple, loader):
    if not recommendations_service.is_configured():
        await context.bot.send_message(chat_id, t(chat_id, "recommendations_fallback"))
        return

    if cache_key in recommendations_result_cache:
        recommendations = recommendations_result_cache[cache_key]
    else:
        recommendations = await asyncio.to_thread(loader)
        recommendations_result_cache[cache_key] = recommendations

    if not recommendations:
        await context.bot.send_message(chat_id, t(chat_id, "recommendations_not_found"))
        return

    session_id = create_result_session(
        str(chat_id),
        {"tracks": "similar_tracks", "artists": "similar_artists"}.get(cache_key[0], cache_key[0]),
        cache_key[1],
        recommendations,
        title_key,
    )
    await context.bot.send_message(
        chat_id,
        t(chat_id, title_key),
        reply_markup=result_page_keyboard(str(chat_id), session_id, result_sessions[session_id]),
    )


async def send_intent_results(chat_id, context, query: str, tags: list[str], exclusions: list[str]):
    if not recommendations_service.is_configured():
        await context.bot.send_message(chat_id, t(chat_id, "recommendations_fallback"))
        return

    # Both typed mood requests and mood buttons arrive here as Last.fm tag searches.
    cache_key = ("intent", tuple(tags), tuple(exclusions))
    if cache_key in recommendations_result_cache:
        recommendations = recommendations_result_cache[cache_key]
    else:
        recommendations = await asyncio.to_thread(
            recommendations_service.get_tracks_by_tags,
            tags,
            exclusions,
            RESULTS_FETCH_LIMIT,
        )
        recommendations_result_cache[cache_key] = recommendations

    if not recommendations:
        await context.bot.send_message(chat_id, t(chat_id, "recommendations_not_found"))
        return

    session_id = create_result_session(
        str(chat_id),
        "intent",
        query,
        recommendations,
        "intent_tracks_title",
    )
    await context.bot.send_message(
        chat_id,
        t(chat_id, "intent_tracks_title"),
        reply_markup=result_page_keyboard(str(chat_id), session_id, result_sessions[session_id]),
    )


async def render_track_menu(chat_id, context, track: dict):
    video_id = track["id"]
    current_track[str(chat_id)] = track
    track_action_cache[video_id] = track

    await context.bot.send_message(
        chat_id,
        t(
            chat_id,
            "selected_track_info",
            title=track["title"],
            artist=track["artist"],
            duration=format_duration(track.get("duration", 0)),
        ),
        reply_markup=track_actions_keyboard(video_id, action_texts(chat_id)),
    )


async def on_similar_track_click(q, context, chat_id: str, video_id: str):
    track = get_action_track(chat_id, video_id)
    if not track:
        await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
        return

    await q.answer()
    await send_recommendation_results(
        chat_id,
        context,
        "similar_tracks_title",
        ("tracks", track["query"]),
        lambda: recommendations_service.get_similar_tracks(track["query"], RESULTS_FETCH_LIMIT),
    )


async def on_similar_artist_click(q, context, chat_id: str, video_id: str):
    track = get_action_track(chat_id, video_id)
    if not track:
        await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
        return

    await q.answer()
    await send_recommendation_results(
        chat_id,
        context,
        "similar_artists_title",
        ("artists", track["query"]),
        lambda: recommendations_service.get_similar_artists(track["query"], RESULTS_FETCH_LIMIT),
    )


async def on_lyrics_click(q, context, chat_id: str, video_id: str):
    track = get_action_track(chat_id, video_id)
    if not track:
        await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
        return

    await q.answer()
    status_msg = await context.bot.send_message(chat_id, t(chat_id, "lyrics_searching"))
    # Video titles often contain channel or release suffixes that reduce lyrics matches.
    lyrics_artist = track.get("lyrics_artist") or clean_lyrics_artist(
        track.get("artist", ""),
        track.get("title", ""),
    )
    lyrics_title = track.get("lyrics_title") or clean_lyrics_title(track.get("title", ""))
    debug("lyrics current_track:", track)
    debug(f"lyrics query: {lyrics_artist} - {lyrics_title}")
    lyrics = await asyncio.to_thread(lyrics_service.get_lyrics, lyrics_artist, lyrics_title)
    debug("lyrics result found" if lyrics else "lyrics result not found")

    if not lyrics:
        await context.bot.edit_message_text(
            t(chat_id, "lyrics_unavailable"),
            chat_id,
            status_msg.message_id,
        )
        return

    max_message_length = 3900
    text = f"{t(chat_id, 'lyrics_title')}\n\n{lyrics}"
    if len(text) <= max_message_length:
        await context.bot.edit_message_text(text, chat_id, status_msg.message_id)
        return

    await context.bot.edit_message_text(text[:max_message_length], chat_id, status_msg.message_id)
    for start in range(max_message_length, len(text), max_message_length):
        await context.bot.send_message(chat_id, text[start:start + max_message_length])


async def on_click_select_track(q, context, chat_id: str, video_id: str):
    track = track_action_cache.get(video_id)
    if not track:
        await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
        return

    await q.answer()
    player_info = await send_player(chat_id, context, video_id, track["title"], show_controls=False)
    if not player_info:
        return

    track = build_track_context(
        chat_id,
        video_id,
        player_info["title"],
        track["artist"],
        player_info["duration"],
    )
    await render_track_menu(chat_id, context, track)


# ─── Free-text search ───────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    query = update.message.text

    if chat_id in pending_playlist_save or chat_id in pending_playlist_create:
        name = query.strip()
        if not name:
            await update.message.reply_text(t(chat_id, "playlist_name_empty"))
            return
        if len(name) > 60:
            await update.message.reply_text(t(chat_id, "playlist_name_too_long"))
            return
        if any(item["name"].casefold() == name.casefold() for item in playlists.get(chat_id, [])):
            await update.message.reply_text(t(chat_id, "playlist_exists", name=name))
            return

        playlist = create_playlist(chat_id, name)
        if chat_id in pending_playlist_save:
            vid = pending_playlist_save.pop(chat_id)
            add_track_to_playlist(chat_id, playlist["id"], vid)
            await update.message.reply_text(t(chat_id, "saved_to_playlist", name=playlist["name"]))
        else:
            pending_playlist_create.discard(chat_id)
            save_playlists()
            await update.message.reply_text(
                t(chat_id, "playlist_created", name=playlist["name"]),
                reply_markup=playlist_view_keyboard(chat_id, playlist["id"], []),
            )
        return

    # A mood/activity request uses recommendations; literal input follows YouTube search.
    intent = await asyncio.to_thread(ai_service.parse_music_intent, query)
    if intent.mode == "tag_search":
        await send_intent_results(chat_id, context, query, intent.tags, intent.exclusions)
        return

    search_key = query.strip().casefold()
    if search_key in search_result_cache:
        results = search_result_cache[search_key]
    else:
        results = await asyncio.to_thread(search_youtube, query)
        search_result_cache[search_key] = results

    if not results:
        await update.message.reply_text(t(chat_id, "not_found"))
        return

    # Preserve track context so actions are enabled only after selection.
    for r in results:
        song_cache[r["id"]] = r["title"]
        artist = r.get("artist") or t(chat_id, "unknown_artist")
        track_action_cache[r["id"]] = build_track_context(chat_id, r["id"], r["title"], artist)

    session_id = create_result_session(chat_id, "search", query, results, "choose_track")
    await update.message.reply_text(
        t(chat_id, "choose_track"),
        reply_markup=result_page_keyboard(chat_id, session_id, result_sessions[session_id]),
    )

# ─── Telegram callbacks ─────────────────────────────────────────────────────
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route inline keyboard actions without blocking the main update handler."""
    q = update.callback_query

    data    = q.data
    chat_id = str(q.message.chat_id)

    # ── lang:<code> ────────────────────────────────────────────────────────
    if data.startswith("lang:"):
        await q.answer()
        lang = data.split(":", 1)[1]
        if lang in TEXTS:
            user_language[chat_id] = lang
            save_languages()
            await q.edit_message_text(t(chat_id, "language_changed"))
            await context.bot.send_message(
                chat_id,
                t(chat_id, "start"),
                reply_markup=main_menu_keyboard(chat_id),
            )
        return

    # ── my_playlists ───────────────────────────────────────────────────────
    if data == "my_playlists":
        pending_playlist_save.pop(chat_id, None)
        pending_playlist_create.discard(chat_id)
        await q.answer()
        text = t(chat_id, "playlists_title") if playlists.get(chat_id) else t(chat_id, "playlists_empty")
        await q.edit_message_text(text, reply_markup=playlists_keyboard(chat_id))

    # ── search_menu from messages sent before command-only menu ────────────
    elif data == "search_menu":
        pending_playlist_save.pop(chat_id, None)
        pending_playlist_create.discard(chat_id)
        await q.answer()
        await q.edit_message_text(t(chat_id, "search_prompt"))

    # ── music_picker ───────────────────────────────────────────────────────
    elif data == "music_picker":
        pending_playlist_save.pop(chat_id, None)
        pending_playlist_create.discard(chat_id)
        await q.answer()
        await q.edit_message_text(
            t(chat_id, "music_picker_intro"),
            reply_markup=music_picker_keyboard(chat_id),
        )

    # ── pick_music:<preset> ────────────────────────────────────────────────
    elif data.startswith("pick_music:"):
        preset = data.split(":", 1)[1]
        tags = music_picker_tags(preset)
        if not tags:
            await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
            return
        await q.answer()
        await send_intent_results(chat_id, context, preset, tags, [])

    # ── help_menu from messages sent before command-only menu ──────────────
    elif data == "help_menu":
        pending_playlist_save.pop(chat_id, None)
        pending_playlist_create.discard(chat_id)
        await q.answer()
        await q.edit_message_text(t(chat_id, "help_text"))

    # ── language_menu ──────────────────────────────────────────────────────
    elif data == "language_menu":
        pending_playlist_save.pop(chat_id, None)
        pending_playlist_create.discard(chat_id)
        await q.answer()
        await q.edit_message_text(
            t(chat_id, "choose_language"),
            reply_markup=language_keyboard(),
        )

    # ── main_menu ──────────────────────────────────────────────────────────
    elif data == "main_menu":
        pending_playlist_save.pop(chat_id, None)
        pending_playlist_create.discard(chat_id)
        await q.answer()
        await q.edit_message_text(t(chat_id, "start"), reply_markup=main_menu_keyboard(chat_id))

    # ── new_playlist_menu ──────────────────────────────────────────────────
    elif data == "new_playlist_menu":
        pending_playlist_save.pop(chat_id, None)
        pending_playlist_create.add(chat_id)
        await q.answer()
        await q.edit_message_text(
            t(chat_id, "playlist_name_prompt"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t(chat_id, "back"), callback_data="my_playlists")
            ]]),
        )

    # ── results_page:<session_id>:<page> ───────────────────────────────────
    elif data.startswith("results_page:"):
        _, session_id, page_text = data.split(":", 2)
        state = result_sessions.get(session_id)
        if not state or state["chat_id"] != chat_id:
            await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
            return
        total_pages = max(1, math.ceil(len(state["results"]) / RESULTS_PAGE_SIZE))
        page = int(page_text)
        if page < 0 or page >= total_pages:
            await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
            return
        state["current_page"] = page
        await q.answer()
        await q.edit_message_text(
            t(chat_id, state["title_key"]),
            reply_markup=result_page_keyboard(chat_id, session_id, state),
        )

    # ── select:<id> ────────────────────────────────────────────────────────
    elif data.startswith("select:"):
        vid = data.split(":", 1)[1]
        await on_click_select_track(q, context, chat_id, vid)

    # ── play:<id> ──────────────────────────────────────────────────────────
    elif data.startswith("play:"):
        await q.answer()
        vid   = data.split(":")[1]
        title = song_cache.get(vid, "Song")
        await send_player(chat_id, context, vid, title)

    # ── similar_tracks:<id> ────────────────────────────────────────────────
    elif data.startswith("similar_tracks:"):
        vid = data.split(":", 1)[1]
        await on_similar_track_click(q, context, chat_id, vid)

    # ── similar_artists:<id> ───────────────────────────────────────────────
    elif data.startswith("similar_artists:"):
        vid = data.split(":", 1)[1]
        await on_similar_artist_click(q, context, chat_id, vid)

    # ── lyrics:<id> ────────────────────────────────────────────────────────
    elif data.startswith("lyrics:"):
        vid = data.split(":", 1)[1]
        await on_lyrics_click(q, context, chat_id, vid)

    # ── rec:<id> ───────────────────────────────────────────────────────────
    elif data.startswith("rec:"):
        rec_id = data.split(":", 1)[1]
        query = recommendation_cache.get(rec_id)
        if not query:
            await q.answer(t(chat_id, "recommendation_expired"), show_alert=True)
            return

        await q.answer(t(chat_id, "recommendation_opening"), show_alert=False)
        results = await asyncio.to_thread(search_youtube, query)
        if not results:
            await context.bot.send_message(chat_id, t(chat_id, "not_found"))
            return

        first = results[0]
        song_cache[first["id"]] = first["title"]
        artist = first.get("artist") or t(chat_id, "unknown_artist")
        track = build_track_context(chat_id, first["id"], first["title"], artist)
        player_info = await send_player(chat_id, context, first["id"], first["title"], show_controls=False)
        if not player_info:
            return

        track = build_track_context(
            chat_id,
            first["id"],
            player_info["title"],
            artist,
            player_info["duration"],
        )
        await render_track_menu(chat_id, context, track)

    # ── save:<id> ──────────────────────────────────────────────────────────
    elif data.startswith("save:"):
        vid = data.split(":", 1)[1]
        pending_playlist_save.pop(chat_id, None)
        pending_playlist_create.discard(chat_id)
        user_playlists = playlists.get(chat_id, [])
        if user_playlists:
            buttons = [
                [InlineKeyboardButton(item["name"][:45], callback_data=f"save_to:{item['id']}:{vid}")]
                for item in user_playlists
            ]
            buttons.append([
                InlineKeyboardButton(t(chat_id, "new_playlist"), callback_data=f"create_playlist:{vid}")
            ])
            prompt = t(chat_id, "choose_playlist")
        else:
            buttons = [[
                InlineKeyboardButton(t(chat_id, "create_playlist"), callback_data=f"create_playlist:{vid}")
            ]]
            prompt = t(chat_id, "no_playlists_for_save")
        buttons.append([
            InlineKeyboardButton(t(chat_id, "cancel"), callback_data="cancel_save")
        ])
        await q.answer()
        await context.bot.send_message(
            chat_id,
            prompt,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    # ── create_playlist:<id> ───────────────────────────────────────────────
    elif data.startswith("create_playlist:"):
        pending_playlist_create.discard(chat_id)
        pending_playlist_save[chat_id] = data.split(":", 1)[1]
        await q.answer()
        await q.edit_message_text(
            t(chat_id, "playlist_name_prompt"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t(chat_id, "cancel"), callback_data="cancel_save")
            ]]),
        )

    # ── cancel_save ────────────────────────────────────────────────────────
    elif data == "cancel_save":
        pending_playlist_save.pop(chat_id, None)
        pending_playlist_create.discard(chat_id)
        await q.answer()
        await q.edit_message_text(t(chat_id, "save_cancelled"))

    # ── save_to:<playlist_id>:<id> ─────────────────────────────────────────
    elif data.startswith("save_to:"):
        _, playlist_id, vid = data.split(":", 2)
        playlist = next(
            (item for item in playlists.get(chat_id, []) if item["id"] == playlist_id),
            None,
        )
        if not playlist:
            await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
            return

        if not add_track_to_playlist(chat_id, playlist_id, vid):
            await q.answer(
                t(chat_id, "already_in_playlist", name=playlist["name"]),
                show_alert=True,
            )
            return

        await q.answer(t(chat_id, "saved_to_playlist", name=playlist["name"]))
        await q.edit_message_text(t(chat_id, "saved_to_playlist", name=playlist["name"]))

    # ── saved:<id> from buttons sent before playlists were introduced ───────
    elif data.startswith("saved:"):
        await q.answer(t(chat_id, "already_saved"), show_alert=False)

    # ── open_playlist:<playlist_id> ────────────────────────────────────────
    elif data.startswith("open_playlist:"):
        playlist_id = data.split(":", 1)[1]
        playlist = get_playlist(chat_id, playlist_id)
        if not playlist:
            await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
            return

        tracks = playlist_tracks.get(chat_id, {}).get(playlist_id, [])
        await q.answer()
        for track in tracks:
            song_cache[track["id"]] = track["title"]
        await q.edit_message_text(
            playlist_detail_text(chat_id, playlist, tracks),
            reply_markup=playlist_view_keyboard(chat_id, playlist_id, tracks),
        )

    # ── edit_playlist:<playlist_id> ────────────────────────────────────────
    elif data.startswith("edit_playlist:"):
        playlist_id = data.split(":", 1)[1]
        playlist = get_playlist(chat_id, playlist_id)
        if not playlist:
            await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
            return

        tracks = playlist_tracks.get(chat_id, {}).get(playlist_id, [])
        text = (
            t(chat_id, "edit_playlist_title", name=playlist["name"])
            if tracks
            else t(chat_id, "playlist_empty", name=playlist["name"])
        )
        await q.answer()
        await q.edit_message_text(
            text,
            reply_markup=playlist_edit_keyboard(chat_id, playlist_id, tracks),
        )

    # ── confirm_delete_playlist:<playlist_id> ──────────────────────────────
    elif data.startswith("confirm_delete_playlist:"):
        playlist_id = data.split(":", 1)[1]
        playlist = get_playlist(chat_id, playlist_id)
        if not playlist:
            await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
            return
        await q.answer()
        await q.edit_message_text(
            t(chat_id, "confirm_delete_playlist", name=playlist["name"]),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t(chat_id, "confirm"), callback_data=f"delete_playlist:{playlist_id}")],
                [InlineKeyboardButton(t(chat_id, "cancel"), callback_data=f"open_playlist:{playlist_id}")],
            ]),
        )

    # ── delete_playlist:<playlist_id> ──────────────────────────────────────
    elif data.startswith("delete_playlist:"):
        playlist_id = data.split(":", 1)[1]
        playlist = get_playlist(chat_id, playlist_id)
        if not playlist:
            await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
            return
        playlists[chat_id] = [
            item for item in playlists.get(chat_id, []) if item["id"] != playlist_id
        ]
        playlist_tracks.setdefault(chat_id, {}).pop(playlist_id, None)
        save_playlists()
        await q.answer()
        await q.edit_message_text(
            t(chat_id, "playlist_deleted", name=playlist["name"]),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t(chat_id, "back"), callback_data="my_playlists")
            ]]),
        )

    # ── confirm_remove_track:<playlist_id>:<id> ────────────────────────────
    elif data.startswith("confirm_remove_track:"):
        _, playlist_id, vid = data.split(":", 2)
        playlist = get_playlist(chat_id, playlist_id)
        tracks = playlist_tracks.get(chat_id, {}).get(playlist_id, [])
        track = next((item for item in tracks if item["id"] == vid), None)
        if not playlist or not track:
            await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
            return
        await q.answer()
        await q.edit_message_text(
            t(chat_id, "confirm_remove_track", title=track["title"], name=playlist["name"]),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t(chat_id, "confirm"), callback_data=f"remove_track:{playlist_id}:{vid}")],
                [InlineKeyboardButton(t(chat_id, "cancel"), callback_data=f"edit_playlist:{playlist_id}")],
            ]),
        )

    # ── remove_track:<playlist_id>:<id> ────────────────────────────────────
    elif data.startswith("remove_track:"):
        _, playlist_id, vid = data.split(":", 2)
        playlist = get_playlist(chat_id, playlist_id)
        tracks = playlist_tracks.get(chat_id, {}).get(playlist_id, [])
        track = next((item for item in tracks if item["id"] == vid), None)
        if not playlist or not track:
            await q.answer(t(chat_id, "actions_track_expired"), show_alert=True)
            return
        playlist_tracks[chat_id][playlist_id] = [
            item for item in tracks if item["id"] != vid
        ]
        save_playlists()
        remaining_tracks = playlist_tracks[chat_id][playlist_id]
        text = (
            t(chat_id, "track_removed", title=track["title"], name=playlist["name"])
            if remaining_tracks
            else t(chat_id, "playlist_empty", name=playlist["name"])
        )
        await q.answer()
        await q.edit_message_text(
            text,
            reply_markup=playlist_edit_keyboard(chat_id, playlist_id, remaining_tracks),
        )

async def configure_bot_menu(app):
    command_sets = {
        None: [
            BotCommand("start", "Відкрити головне меню"),
            BotCommand("search", "Знайти пісню"),
            BotCommand("playlists", "Мої плейлісти"),
            BotCommand("mood", "Підібрати музику"),
            BotCommand("language", "Змінити мову"),
            BotCommand("help", "Допомога"),
        ],
        "uk": [
            BotCommand("start", "Відкрити головне меню"),
            BotCommand("search", "Знайти пісню"),
            BotCommand("playlists", "Мої плейлісти"),
            BotCommand("mood", "Підібрати музику"),
            BotCommand("language", "Змінити мову"),
            BotCommand("help", "Допомога"),
        ],
        "ru": [
            BotCommand("start", "Открыть главное меню"),
            BotCommand("search", "Найти песню"),
            BotCommand("playlists", "Мои плейлисты"),
            BotCommand("mood", "Подобрать музыку"),
            BotCommand("language", "Изменить язык"),
            BotCommand("help", "Помощь"),
        ],
        "en": [
            BotCommand("start", "Open main menu"),
            BotCommand("search", "Search for a song"),
            BotCommand("playlists", "Open my playlists"),
            BotCommand("mood", "Pick music by mood"),
            BotCommand("language", "Change language"),
            BotCommand("help", "Show help"),
        ],
    }
    for language_code, commands in command_sets.items():
        await app.bot.set_my_commands(commands, language_code=language_code)
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())


# ─── Application entry point ─────────────────────────────────────────────────
if __name__ == "__main__":
    validate_configuration()
    load_saved()
    load_languages()
    load_playlists()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(configure_bot_menu).build()

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("playlists", playlists_command))
    app.add_handler(CommandHandler("mood", mood_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("saved",   saved))
    app.add_handler(CommandHandler("language", language))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_click))

    print("🚀 BOT STARTED")
    app.run_polling()
