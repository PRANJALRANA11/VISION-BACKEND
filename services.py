# services.py
from typing import List
from models import Item

# Simulating a database with a list
_items_db: List[Item] = []

def get_all_items() -> List[Item]:
    return _items_db

def get_item_by_id(item_id: int) -> Item | None:
    return next((item for item in _items_db if item.id == item_id), None)

def add_item(item: Item) -> Item:
    if get_item_by_id(item.id):
        raise ValueError("Item with this ID already exists")
    _items_db.append(item)
    return item
