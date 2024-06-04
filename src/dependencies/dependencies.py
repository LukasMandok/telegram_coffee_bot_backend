from fastapi import Depends
from ..database.base_repo import BaseRepository
from ..database.motormongo_repo import MotorMongoRepository

from ..models.motormodels import TelegramUserDocument
from ..schemas.user import TelegramUser

def get_repo() -> BaseRepository:
    return MotorMongoRepository