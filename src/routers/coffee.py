from fastapi import APIRouter

router = APIRouter(
    prefix="/coffee",
    tags=["coffee"],
    responses={404: {"description": "Not found"}},
)

@router.get("/")
async def read_coffee():
    return {"message": "Coffee router placeholder"}
