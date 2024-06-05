from fastapi import Depends
from ..database.base_repo import BaseRepository
from ..database.beanie_repo import BeanieRepository

# from ..models.motormongo_models import TelegramUserDocument
from ..schemas.user import TelegramUser

def get_repo() -> BaseRepository:
    return BeanieRepository