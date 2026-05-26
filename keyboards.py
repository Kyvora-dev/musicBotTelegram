from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _append_pagination(
    buttons: list,
    page: int,
    total_pages: int,
    callback_prefix: str | None,
    previous_text: str,
    next_text: str,
):
    if not callback_prefix or total_pages <= 1:
        return

    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(previous_text, callback_data=f"{callback_prefix}:{page - 1}")
        )
    if page < total_pages - 1:
        navigation.append(
            InlineKeyboardButton(next_text, callback_data=f"{callback_prefix}:{page + 1}")
        )
    if navigation:
        buttons.append(navigation)


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇺🇦 Українська", callback_data="lang:uk"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
        ],
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
        ],
    ])


def recommendations_keyboard(
    recommendations: list,
    cache: dict,
    start_index: int = 0,
    page: int = 0,
    total_pages: int = 1,
    callback_prefix: str | None = None,
    previous_text: str = "⬅️ Previous",
    next_text: str = "Next ➡️",
) -> InlineKeyboardMarkup:
    buttons = []
    for index, rec in enumerate(recommendations, start=start_index):
        rec_id = str(index)
        cache[rec_id] = rec.query
        buttons.append([
            InlineKeyboardButton(rec.label[:60], callback_data=f"rec:{rec_id}")
        ])
    _append_pagination(buttons, page, total_pages, callback_prefix, previous_text, next_text)
    return InlineKeyboardMarkup(buttons)


def search_results_keyboard(
    results: list,
    page: int = 0,
    total_pages: int = 1,
    callback_prefix: str | None = None,
    previous_text: str = "⬅️ Previous",
    next_text: str = "Next ➡️",
) -> InlineKeyboardMarkup:
    buttons = []
    for item in results:
        title = item["title"][:45]
        buttons.append([
            InlineKeyboardButton(f"🎵 {title}", callback_data=f"select:{item['id']}"),
        ])
    _append_pagination(buttons, page, total_pages, callback_prefix, previous_text, next_text)
    return InlineKeyboardMarkup(buttons)


def track_actions_keyboard(video_id: str, texts: dict, is_saved: bool = False) -> InlineKeyboardMarkup:
    save_text = texts["saved"] if is_saved else texts["save"]
    save_callback = f"saved:{video_id}" if is_saved else f"save:{video_id}"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(texts["similar_tracks"], callback_data=f"similar_tracks:{video_id}"),
            InlineKeyboardButton(texts["similar_artists"], callback_data=f"similar_artists:{video_id}"),
        ],
        [
            InlineKeyboardButton(texts["lyrics"], callback_data=f"lyrics:{video_id}"),
            InlineKeyboardButton(save_text, callback_data=save_callback),
        ],
    ])
