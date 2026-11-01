from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import AstDocument, Node


def parse_integer(text: bytes) -> int:
    result = 0
    for start in range(0, len(text), 9):
        part = text[start:start + 9]
        result = result * (10 ** len(part)) + int(part)
    return result


def integer_text(value: int) -> bytes:
    if value.bit_length() < 12000:
        return str(value).encode("ascii")
    negative = value < 0
    value = abs(value)
    parts = []
    while value:
        value, remainder = divmod(value, 1_000_000_000)
        parts.append(remainder)
    result = str(parts.pop()) + "".join(f"{part:09d}" for part in reversed(parts))
    return (("-" if negative else "") + result).encode("ascii")


def canonical_key(key):
    if type(key) not in {int, float, bytes}:
        raise ValueError("Dictionary keys must be integers, floats or byte strings")
    if isinstance(key, float):
        if math.isnan(key):
            raise ValueError("NaN cannot be used as a dictionary key")
        if math.isfinite(key) and key.is_integer():
            return int(key)
    return key


def compare_keys(left, right) -> int:
    if isinstance(left, bytes) != isinstance(right, bytes):
        return 1 if isinstance(left, bytes) else -1
    return (left > right) - (left < right)


def priority(key) -> int:
    encoded = b"S" + key if isinstance(key, bytes) else b"I" + integer_text(key) if isinstance(key, int) else b"F" + repr(key).encode("ascii")
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")


@dataclass(frozen=True)
class Tree:
    key: Any
    value: Any
    priority: int
    left: Tree | None = None
    right: Tree | None = None
    size: int = 1


def branch(key, value, rank, left=None, right=None):
    return Tree(key, value, rank, left, right, 1 + (left.size if left else 0) + (right.size if right else 0))


def split(tree, key):
    if tree is None:
        return None, None
    if compare_keys(tree.key, key) < 0:
        left, right = split(tree.right, key)
        return branch(tree.key, tree.value, tree.priority, tree.left, left), right
    left, right = split(tree.left, key)
    return left, branch(tree.key, tree.value, tree.priority, right, tree.right)


def merge(left, right):
    if left is None:
        return right
    if right is None:
        return left
    if left.priority >= right.priority:
        return branch(left.key, left.value, left.priority, left.left, merge(left.right, right))
    return branch(right.key, right.value, right.priority, merge(left, right.left), right.right)


def insert(tree, key, value, rank):
    if tree is None:
        return branch(key, value, rank)
    comparison = compare_keys(key, tree.key)
    if comparison == 0:
        return branch(key, value, tree.priority, tree.left, tree.right)
    if rank > tree.priority:
        left, right = split(tree, key)
        return branch(key, value, rank, left, right)
    if comparison < 0:
        return branch(tree.key, tree.value, tree.priority, insert(tree.left, key, value, rank), tree.right)
    return branch(tree.key, tree.value, tree.priority, tree.left, insert(tree.right, key, value, rank))


def erase(tree, key):
    if tree is None:
        return None
    comparison = compare_keys(key, tree.key)
    if comparison == 0:
        return merge(tree.left, tree.right)
    if comparison < 0:
        return branch(tree.key, tree.value, tree.priority, erase(tree.left, key), tree.right)
    return branch(tree.key, tree.value, tree.priority, tree.left, erase(tree.right, key))


@dataclass(frozen=True)
class Dictionary:
    root: Tree | None = None

    def with_item(self, key, value) -> Dictionary:
        key = canonical_key(key)
        return Dictionary(erase(self.root, key) if value is None else insert(self.root, key, value, priority(key)))

    def find(self, key):
        key = canonical_key(key)
        tree = self.root
        while tree is not None:
            comparison = compare_keys(key, tree.key)
            if comparison == 0:
                return tree.value
            tree = tree.left if comparison < 0 else tree.right
        return None

    def __len__(self):
        return self.root.size if self.root else 0

    def items(self):
        pending = []
        tree = self.root
        while pending or tree is not None:
            while tree is not None:
                pending.append(tree)
                tree = tree.left
            tree = pending.pop()
            yield tree.key, tree.value
            tree = tree.right


@dataclass(frozen=True, eq=False)
class Function:
    node: Node
    environment: dict[bytes, Any]
    document: AstDocument
    origin: Path | None


def equal(left, right) -> bool:
    if type(left) in {int, float} and type(right) in {int, float}:
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, Dictionary):
        return len(left) == len(right) and all(compare_keys(left_key, right_key) == 0 and equal(left_value, right_value) for (left_key, left_value), (right_key, right_value) in zip(left.items(), right.items()))
    if isinstance(left, Function):
        return left is right
    return left == right


def environment_dictionary(environment: dict[bytes, Any]) -> Dictionary:
    result = Dictionary()
    for name, value in environment.items():
        result = result.with_item(name, value)
    return result


def environment_names(environment: Dictionary) -> dict[bytes, Any]:
    return {key: value for key, value in environment.items() if isinstance(key, bytes)}
