from schema.user import TelegramUser


def individal_serial(telegramuser: TelegramUser) -> dict:
    return {
        'id': telegramuser.id,
        'first_name': telegramuser.first_name,
        'last_name': telegramuser.last_name,
        'username': telegramuser.username,
        'last_login': telegramuser.last_login,
        'phone': telegramuser.phone,
        'photo_id': telegramuser.photo_id,
        'lang_code': telegramuser.lang_code,
    }
    
def list_serial(telegramusers: list[TelegramUser]) -> list[dict]:
    return [individal_serial(telegramuser) for telegramuser in telegramusers]