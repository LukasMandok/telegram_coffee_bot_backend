from datetime import datetime
from pydantic import BaseModel
# class Chat(BaseModel):
#     chat_id: int

class BasePassword(BaseModel):
    password: str
    
class BaseUser(BaseModel):
    first_name: str
    last_name: str
    
class TelegramUser(BaseUser):
    user_id: int
    username: str
    last_login: datetime
    phone: str
    photo_id: int
    lang_code: str
    

    