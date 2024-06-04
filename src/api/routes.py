from fastapi import APIRouter, HTTPException

from ..schemas.user import TelegramUser
# from ..models.motormodels import TelegramUserDocument

from ..handlers.handlers import *

router = APIRouter()

@router.post("/login/", status_code = 200)
async def login_user(id: int):
    return check_user(id)

@router.post("/auth/", status_code = 201)
async def authenticate(password):
    return check_password(password)

@router.post("/users/", status_code = 202)
async def create_user():
    pass


### retrieve values from the database ###

@router.get("/users")
async def get_users():
    users = await get_all_users()
    if not users:
        raise HTTPException(status_code = 404, detail = "No users found")
    # QUESTION: maybe just return users as is?  
    return [user.to_dict() for user in users]