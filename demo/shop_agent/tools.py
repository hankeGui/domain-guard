"""Mock tools that the demo agent can call. Backed by JSON files in demo/data/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> Any:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def get_order(order_id: str, user_id: str | None = None) -> dict:
    """Look up an order by ID. Returns the order dict or {error: ...}."""
    orders = _load("orders.json")
    for o in orders:
        if o["order_id"].lower() == order_id.lower():
            if user_id and o["user_id"] != user_id:
                return {"error": "order_not_owned_by_user", "order_id": order_id}
            return o
    return {"error": "not_found", "order_id": order_id}


def list_orders(user_id: str) -> list[dict]:
    """All orders belonging to a user."""
    orders = _load("orders.json")
    return [o for o in orders if o["user_id"] == user_id]


def get_shipment(shipment_id: str) -> dict:
    """Look up a shipment by tracking number."""
    shipments = _load("shipments.json")
    s = shipments.get(shipment_id) or shipments.get(shipment_id.upper())
    if s is None:
        return {"error": "shipment_not_found", "shipment_id": shipment_id}
    return s


def submit_return(order_id: str, reason: str, user_id: str | None = None) -> dict:
    """Mock: pretend to file a return request. (No write to the JSON file —
    the demo treats this as idempotent and side-effect-free.)"""
    order = get_order(order_id, user_id=user_id)
    if "error" in order:
        return order
    return {
        "ok": True,
        "order_id": order_id,
        "reason": reason,
        "rma_id": f"RMA-{order_id[-4:]}-{abs(hash(reason)) % 1000:03d}",
        "expected_refund_days": 7,
    }


# ---- Tool descriptions for LLM tool-calling ----

TOOL_SPECS = [
    {
        "name": "get_order",
        "description": "Look up a single order by its ID (e.g. ORD-1001). "
                       "Returns order details including status, items, total, shipment_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID like ORD-1001"}
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "list_orders",
        "description": "List all orders belonging to the current user. "
                       "Use this when the user asks about their orders without giving an ID.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_shipment",
        "description": "Look up a shipment / tracking record by its ID (e.g. SF-9988-7766). "
                       "Returns current location, status, and event history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "string",
                                "description": "Carrier tracking ID like SF-9988-7766"}
            },
            "required": ["shipment_id"],
        },
    },
    {
        "name": "submit_return",
        "description": "File a return request for an order. Requires order_id and a "
                       "short reason. Returns an RMA number.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "reason": {"type": "string", "description": "Reason for the return"},
            },
            "required": ["order_id", "reason"],
        },
    },
]


def dispatch(name: str, args: dict, user_id: str | None = None) -> dict:
    """Single entry point — keeps the agent loop simple."""
    if name == "get_order":
        return get_order(args["order_id"], user_id=user_id)
    if name == "list_orders":
        if not user_id:
            return {"error": "user_id_required"}
        return {"orders": list_orders(user_id)}
    if name == "get_shipment":
        return get_shipment(args["shipment_id"])
    if name == "submit_return":
        return submit_return(args["order_id"], args["reason"], user_id=user_id)
    return {"error": f"unknown_tool:{name}"}
