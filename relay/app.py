"""HookLine relay: the only thing that ever holds a Lighter API key.

The game (browser) never sees the key. It calls this relay with game-level intents
("open a 5x ETH long with $25 margin, knife at -25%, net at +60%") and the relay turns
them into signed Lighter transactions with the official SignerClient, enforcing hard
server-side limits whatever the game asks for.

Run:  pip install -r requirements.txt && uvicorn app:app --port 8080
Env:  see .env.example (start on testnet).
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Literal, Optional

import httpx
import lighter
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

URL = os.environ.get("LIGHTER_URL", "https://testnet.zklighter.elliot.ai").rstrip("/")
ACCOUNT_INDEX = int(os.environ.get("LIGHTER_ACCOUNT_INDEX", "0") or 0)
API_KEY_INDEX = int(os.environ.get("LIGHTER_API_KEY_INDEX", "3") or 3)
API_PRIVATE_KEY = os.environ.get("LIGHTER_API_PRIVATE_KEY", "")
RELAY_TOKEN = os.environ.get("HOOKLINE_RELAY_TOKEN", "")
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
MAX_MARGIN_USD = float(os.environ.get("MAX_MARGIN_USD", "50"))
MAX_LEVERAGE = int(os.environ.get("MAX_LEVERAGE", "20"))
ALLOWED_MARKETS = {m.strip() for m in os.environ.get("ALLOWED_MARKETS", "").split(",") if m.strip()}
MAX_SLIPPAGE = float(os.environ.get("MAX_SLIPPAGE", "0.01"))
INTEGRATOR_ACCOUNT_INDEX = int(os.environ.get("INTEGRATOR_ACCOUNT_INDEX", "0") or 0)
INTEGRATOR_TAKER_FEE = int(os.environ.get("INTEGRATOR_TAKER_FEE", "0") or 0)

app = FastAPI(title="HookLine relay")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["authorization", "content-type"])

_client: Optional[lighter.SignerClient] = None
_markets: dict[str, dict] = {}          # symbol -> {id, size_decimals, price_decimals, min_base, min_quote}
_markets_at = 0.0
_lock = asyncio.Lock()                  # one signed tx at a time keeps nonces simple
_http = httpx.AsyncClient(base_url=URL, timeout=10)


def auth(authorization: str = Header(default="")) -> None:
    if not RELAY_TOKEN:
        raise HTTPException(503, "relay has no HOOKLINE_RELAY_TOKEN configured")
    if authorization != f"Bearer {RELAY_TOKEN}":
        raise HTTPException(401, "bad token")


def signer() -> lighter.SignerClient:
    global _client
    if not API_PRIVATE_KEY or not ACCOUNT_INDEX:
        raise HTTPException(503, "relay is not configured with a Lighter account / API key")
    if _client is None:
        _client = lighter.SignerClient(url=URL, account_index=ACCOUNT_INDEX, api_private_keys={API_KEY_INDEX: API_PRIVATE_KEY})
        err = _client.check_client()
        if err:
            _client = None
            raise HTTPException(503, f"Lighter API key check failed: {err}")
    return _client


async def markets() -> dict[str, dict]:
    global _markets, _markets_at
    if _markets and time.time() - _markets_at < 600:
        return _markets
    r = await _http.get("/api/v1/orderBookDetails")
    r.raise_for_status()
    out = {}
    for o in r.json().get("order_book_details", []):
        if o.get("market_type") != "perp" or o.get("status") != "active":
            continue
        out[o["symbol"]] = {
            "id": o["market_id"], "size_decimals": o["size_decimals"], "price_decimals": o["price_decimals"],
            "min_base": float(o["min_base_amount"]), "min_quote": float(o["min_quote_amount"]),
            "max_leverage": max(1, round(10000 / max(1, o.get("min_initial_margin_fraction", 1000)))),
            "mark": float(o.get("mark_price") or o.get("last_trade_price") or 0),
        }
    _markets, _markets_at = out, time.time()
    return out


async def market(symbol: str) -> dict:
    if ALLOWED_MARKETS and symbol not in ALLOWED_MARKETS:
        raise HTTPException(403, f"{symbol} is not enabled on this relay")
    ms = await markets()
    if symbol not in ms:
        raise HTTPException(404, f"unknown market {symbol}")
    return ms[symbol]


async def account() -> dict:
    r = await _http.get("/api/v1/account", params={"by": "index", "value": str(ACCOUNT_INDEX)})
    r.raise_for_status()
    accts = r.json().get("accounts") or []
    return accts[0] if accts else {}


def _px(m: dict, price: float) -> int:
    return int(round(price * 10 ** m["price_decimals"]))


def _base(m: dict, size: float) -> int:
    return int(size * 10 ** m["size_decimals"])


def _coi() -> int:
    return int(time.time() * 1000) % (2 ** 31)


async def _tx(coro):
    tx, resp, err = await coro
    if err:
        raise HTTPException(400, f"Lighter rejected the transaction: {err}")
    return {"tx_hash": getattr(resp, "tx_hash", None)}


class OpenReq(BaseModel):
    market: str
    side: Literal[1, -1]                  # 1 = long, -1 = short
    margin_usd: float = Field(gt=0)
    leverage: int = Field(ge=1)
    stop_frac: Optional[float] = None     # knife: PnL fraction of margin, e.g. -0.25
    tp_frac: Optional[float] = None       # net: PnL fraction of margin, e.g. 0.6


class CloseReq(BaseModel):
    market: str
    fraction: float = Field(default=1.0, gt=0, le=1)


class StopReq(BaseModel):
    market: str
    stop_frac: float


class TpReq(BaseModel):
    market: str
    tp_frac: float


@app.get("/v1/health")
async def health():
    return {"ok": True, "lighter": URL, "account": ACCOUNT_INDEX or None, "configured": bool(API_PRIVATE_KEY and ACCOUNT_INDEX and RELAY_TOKEN),
            "limits": {"max_margin_usd": MAX_MARGIN_USD, "max_leverage": MAX_LEVERAGE, "markets": sorted(ALLOWED_MARKETS) or "all"}}


@app.get("/v1/markets")
async def get_markets():
    return await markets()


@app.get("/v1/positions", dependencies=[Depends(auth)])
async def positions():
    a = await account()
    out = []
    for p in a.get("positions") or []:
        size = float(p.get("position") or 0)
        if size == 0:
            continue
        out.append({"market": p.get("symbol"), "market_id": p.get("market_id"), "side": 1 if int(p.get("sign", 1)) > 0 else -1, "size": size,
                    "entry": float(p.get("avg_entry_price") or 0), "upnl": float(p.get("unrealized_pnl") or 0),
                    "liq": float(p.get("liquidation_price") or 0), "margin": float(p.get("allocated_margin") or 0)})
    return {"collateral": float(a.get("collateral") or 0), "available": float(a.get("available_balance") or 0), "positions": out}


@app.post("/v1/open", dependencies=[Depends(auth)])
async def open_position(req: OpenReq):
    m = await market(req.market)
    lev = min(req.leverage, MAX_LEVERAGE, m["max_leverage"])
    margin = min(req.margin_usd, MAX_MARGIN_USD)
    notional = margin * lev
    if notional < m["min_quote"]:
        raise HTTPException(400, f"position too small: ${notional:.2f} < Lighter minimum ${m['min_quote']:.2f}")
    c = signer()
    async with _lock:
        await _tx(c.update_leverage(m["id"], c.ISOLATED_MARGIN_MODE, lev, api_key_index=API_KEY_INDEX))
        await _tx(c.create_market_order_quote_amount(m["id"], _coi(), notional, MAX_SLIPPAGE, req.side < 0,
                                                     integrator_account_index=INTEGRATOR_ACCOUNT_INDEX, integrator_taker_fee=INTEGRATOR_TAKER_FEE,
                                                     api_key_index=API_KEY_INDEX))
    # read back the fill, then hang the knife and the net on it as reduce-only triggers
    pos = None
    for _ in range(10):
        await asyncio.sleep(0.4)
        pos = next((p for p in (await positions())["positions"] if p["market"] == req.market), None)
        if pos:
            break
    if not pos:
        raise HTTPException(502, "order sent but no position showed up yet; check /v1/positions")
    out = {"position": pos, "leverage": lev, "margin_usd": margin}
    if req.stop_frac is not None:
        out["stop"] = await _set_stop(m, pos, lev, req.stop_frac)
    if req.tp_frac is not None:
        out["tp"] = await _set_tp(m, pos, lev, req.tp_frac)
    return out


async def _set_stop(m, pos, lev, frac):
    trig = pos["entry"] * (1 + pos["side"] * frac / lev)
    worst = trig * (1 - pos["side"] * MAX_SLIPPAGE * 3)
    c = signer()
    async with _lock:
        r = await _tx(c.create_sl_order(m["id"], _coi(), _base(m, pos["size"]), _px(m, trig), _px(m, worst), pos["side"] > 0, reduce_only=True, api_key_index=API_KEY_INDEX))
    return {"trigger": trig, **r}


async def _set_tp(m, pos, lev, frac):
    trig = pos["entry"] * (1 + pos["side"] * frac / lev)
    worst = trig * (1 - pos["side"] * MAX_SLIPPAGE * 3)
    c = signer()
    async with _lock:
        r = await _tx(c.create_tp_order(m["id"], _coi(), _base(m, pos["size"]), _px(m, trig), _px(m, worst), pos["side"] > 0, reduce_only=True, api_key_index=API_KEY_INDEX))
    return {"trigger": trig, **r}


@app.post("/v1/stop", dependencies=[Depends(auth)])
async def move_stop(req: StopReq):
    m = await market(req.market)
    pos = next((p for p in (await positions())["positions"] if p["market"] == req.market), None)
    if not pos:
        raise HTTPException(404, "no open position")
    lev = max(1, round(abs(pos["size"] * pos["entry"]) / max(pos["margin"], 1e-9)))
    return await _set_stop(m, pos, lev, req.stop_frac)


@app.post("/v1/tp", dependencies=[Depends(auth)])
async def set_tp(req: TpReq):
    m = await market(req.market)
    pos = next((p for p in (await positions())["positions"] if p["market"] == req.market), None)
    if not pos:
        raise HTTPException(404, "no open position")
    lev = max(1, round(abs(pos["size"] * pos["entry"]) / max(pos["margin"], 1e-9)))
    return await _set_tp(m, pos, lev, req.tp_frac)


@app.post("/v1/close", dependencies=[Depends(auth)])
async def close_position(req: CloseReq):
    m = await market(req.market)
    pos = next((p for p in (await positions())["positions"] if p["market"] == req.market), None)
    if not pos:
        raise HTTPException(404, "no open position")
    size = pos["size"] * req.fraction
    worst = m["mark"] or pos["entry"]
    worst = worst * (1 - pos["side"] * MAX_SLIPPAGE * 2)
    c = signer()
    async with _lock:
        r = await _tx(c.create_market_order(m["id"], _coi(), max(1, _base(m, size)), _px(m, worst), pos["side"] > 0, reduce_only=True,
                                            integrator_account_index=INTEGRATOR_ACCOUNT_INDEX, integrator_taker_fee=INTEGRATOR_TAKER_FEE, api_key_index=API_KEY_INDEX))
    return {"closed": size, "entry": pos["entry"], "upnl_before": pos["upnl"], **r}
