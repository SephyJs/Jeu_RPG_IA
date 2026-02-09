from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class ItemStack:
    item_id: str
    qty: int


@dataclass
class InventoryGrid:
    cols: int
    rows: int
    slots: list[Optional[ItemStack]]

    @classmethod
    def empty(cls, cols: int, rows: int) -> "InventoryGrid":
        return cls(cols=cols, rows=rows, slots=[None] * (cols * rows))

    def index(self, col: int, row: int) -> int:
        return row * self.cols + col

    def get(self, idx: int) -> Optional[ItemStack]:
        return self.slots[idx]

    def set(self, idx: int, stack: Optional[ItemStack]) -> None:
        self.slots[idx] = stack
