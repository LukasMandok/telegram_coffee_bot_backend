from typing import TYPE_CHECKING
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi_utils.cbv import cbv

from ..schemas.user import TelegramUser
from ..dependencies.dependencies import verify_token, get_repo
from ..handlers.handlers import check_user, check_password, get_all_users

if TYPE_CHECKING:
    from ..database.base_repo import BaseRepository

### TODO: add secutiry dependancies with scopes to check, if users are authorized to use the requests
### those can be added to as dependancies to the APIRouter to be valid for all routes: https://fastapi.tiangolo.com/tutorial/bigger-applications/?h=application

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(verify_token)],
    responses={404: {"description": "Not found"}},
)

@cbv(router)
class BasicUsersViews:
    repo: "BaseRepository" = Depends(get_repo)
    
    
# Move login outside of users, as those should require authentication
    @router.post("/login/", status_code = 200)
    async def login_user(self, id: int = Query(...)) -> bool:
        print("called login for id:", id)
        user_exists = await check_user(id, self.repo)
        if not user_exists:
            raise HTTPException(status_code = 404, detail = "User not found")
        else:   
            # TODO: implement proper HTTP response
            return user_exists


    @router.post("/auth/", status_code = 201)
    async def authenticate(self, password: str = Query(...)) -> bool:
        print("called auth with the password:", password)
        return await check_password(password, self.repo)


    @router.post("/", status_code = 202)
    async def create_user(self):
        print("called create user")
        pass


    ### retrieve values from the database ###

    @router.get("/")
    async def get_users(self):
        users = await get_all_users(self.repo)
        if not users:
            raise HTTPException(status_code = 404, detail = "No users found")
        # QUESTION: maybe just return users as is?  
        return [user.to_dict() for user in users]