# HookLine

A multiplayer fishing game where every fish is a leveraged perp position on [Lighter](https://lighter.xyz).

- **Rod** = leverage · **Knife** = stop loss · **Net** = take profit · **Sonar** = information · **Chest** + **boat hold** = how many fish (positions) you can have open.
- Every Lighter perp ticker is its own fish species with a silly name (213 of them: Sir Satoshi Swordfish, Ultrasound Tuna, Dog Wif Fish, Claw-d Crab…). See `tools/roster.py`.
- AX tokens only buy gear (rods, knives, nets, sonar, chests, boats). They never buy a better trade.

## Layout

| Path | What |
| --- | --- |
| `game/index.html` | The whole game (three.js r147, no build step). Open via any static server: `python3 -m http.server -d hookline/game`. |
| `game/models/*.json` | glTF models (embedded buffers) built in Blender. |
| `game/art/*.webp` | 2D art (logo, gear, fish icons, boat renders). |
| `blender/` | Headless Blender (`pip install bpy`) scripts that build the models and icon renders. |
| `relay/` | FastAPI relay that holds the Lighter API key and signs orders with the official `lighter-python` SignerClient. |
| `tools/roster.py` | Pulls Lighter's market list and assigns every ticker a fish species, zone and name. |

## Prices and fills

1. **Prices**: the game reads Lighter's public data directly (websocket `market_stats/all`, REST `exchangeStats` fallback). If neither is reachable it runs a simulator.
2. **Fills**: paper by default. For real fills, deploy `relay/` (start on testnet), then in the game open **Gear → Lighter connection**, paste the relay URL + token, **Test relay**, and switch on **Real fills**.

The relay enforces `MAX_MARGIN_USD`, `MAX_LEVERAGE` and `ALLOWED_MARKETS` server-side, whatever the page asks for. The page never sees the API key.

## Relay quick start

```bash
cd hookline/relay
pip install -r requirements.txt
cp .env.example .env   # fill in LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX, LIGHTER_API_PRIVATE_KEY, HOOKLINE_RELAY_TOKEN
set -a; . ./.env; set +a
uvicorn app:app --port 8080
curl localhost:8080/v1/health
```

Endpoints: `GET /v1/health`, `GET /v1/markets`, `GET /v1/positions`, `POST /v1/open`, `POST /v1/stop`, `POST /v1/tp`, `POST /v1/close` (all but health/markets need `Authorization: Bearer $HOOKLINE_RELAY_TOKEN`).
