# main.py
from fastapi import FastAPI, HTTPException
from typing import List

from models import Item
import services

app = FastAPI()

@app.get("/")
def root():
    return {"message": "API running"}

@app.get("/items/", response_model=List[Item])
def read_items():
    return services.get_all_items()

@app.get("/items/{item_id}", response_model=Item)
def read_item(item_id: int):
    item = services.get_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.post("/items/", response_model=Item)
def create_item(item: Item):
    try:
        return services.add_item(item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
