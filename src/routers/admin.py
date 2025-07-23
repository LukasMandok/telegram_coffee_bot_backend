from fastapi import APIRouter, HTTPException, Query, Depends

from ..dependencies.dependencies import verify_admin

router = APIRouter(
    prefix="/users",
    tags=["users"],
    # dependencies=[Depends(verify_admin)],
    responses={404: {"description": "Not found"}},
)

#dependencies=[Depends(get_query_token)]