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
| `server/` | Node multiplayer server (presence relay, rooms of 40). Run anywhere: `cd server && npm i && npm start`. |
| `relay/` | FastAPI relay that holds the Lighter API key and signs orders with the official `lighter-python` SignerClient. |
| `tools/roster.py` | Pulls Lighter's market list and assigns every ticker a fish species, zone and name. |

## Prices and fills

1. **Prices**: the game reads Lighter's public data directly (websocket `market_stats/all`, REST `exchangeStats` fallback). If neither is reachable it runs a simulator.
2. **Fills**: paper by default. For real fills, deploy `relay/` (start on testnet), then in the game open **Gear → Lighter connection**, paste the relay URL + token, **Test relay**, and switch on **Real fills**.

The relay enforces `MAX_MARGIN_USD`, `MAX_LEVERAGE` and `ALLOWED_MARKETS` server-side, whatever the page asks for. The page never sees the API key.

## Relay quick start

```bash
cd hookline/relay
pip install -r requirements.txt   # lighter-sdk from PyPI
cp .env.example .env   # fill in LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX, LIGHTER_API_PRIVATE_KEY, HOOKLINE_RELAY_TOKEN
set -a; . ./.env; set +a
uvicorn app:app --port 8080
curl localhost:8080/v1/health
```

Endpoints: `GET /v1/health`, `GET /v1/markets`, `GET /v1/positions`, `POST /v1/open`, `POST /v1/stop`, `POST /v1/tp`, `POST /v1/close` (all but health/markets need `Authorization: Bearer $HOOKLINE_RELAY_TOKEN`).

## Multiplayer

- **Inside claude.ai** the game uses the page's built-in room: everyone the artifact is shared with sails together.
- **Hosted on your own site**: deploy `server/` (any Node host: Railway, Fly, Render), then point the game at it with `?mp=wss://your-server` or by setting `window.HOOKLINE_MP = 'wss://your-server'` in `game/index.html`. With no server it falls back to bots.

Everyone shares the same 5-minute rounds and Frenzy zone (both are derived from the clock), the round leaderboard comes from each player's presence, and emotes ride along with it.

## Game loop

1. Pick a boat, set sail. A guided first catch walks new players through drive → cast → pull/cut → Fishdex.
2. Drive to a zone, hold to cast (release in the bright band for a PERFECT cast), fight the fish: green past +2% pull it in, red cut it before the Kraken.
3. Every catch fills the Fishdex (213 species), scores the current round and progresses daily quests.
4. Quests pay AX; AX buys rods, knives, nets, sonar, chests and boats.
5. Every 5 minutes: podium, XP, crown for the winner. Mid-round, one zone goes into Frenzy.

## Live deployment (Railway project "hookline")

| Service | URL | Notes |
| --- | --- | --- |
| `hookline` (game + multiplayer) | https://hookline-production-1a40.up.railway.app | redeploys on pushes touching `game/`, `server/`, `package.json` |
| `relay` (Lighter signer) | https://relay-production-ef74.up.railway.app | root `relay/`, **testnet**, limits $25 margin / 10x / BTC,ETH,SOL, CORS locked to the game |

To turn on real (testnet) fills, add these variables to the **relay** service in the Railway dashboard (never commit them):
`LIGHTER_ACCOUNT_INDEX`, `LIGHTER_API_PRIVATE_KEY` (and `LIGHTER_API_KEY_INDEX` if not 3), `HOOKLINE_RELAY_TOKEN` (any long random string).
Then in the game: Gear → Lighter connection → paste the token → Test relay → Real fills.
In real mode the relay's markets are priced from the relay's own network (`/v1/prices`), so testnet PnL is honest.

## Play for real (Privy + Lighter, in the browser)

- `game/hookline-real.js` is the real adapter: Privy sign-in (`game/hookline-auth/`, built from `auth/`), Lighter's official WASM signer in a Web Worker (`game/lighter/`), and the device's Lighter API key kept encrypted in IndexedDB. Trades go from the player's browser straight to Lighter; HookLine never holds keys or funds.
- It turns on when the game service has `PRIVY_APP_ID` set (served to the page via `/config.js`); without it the game keeps the clearly-labeled DEMO adapter.
- `LIGHTER_NET` (default `testnet`). Testnet funding uses Lighter's faucet via `/api/faucet`. Mainnet deposits (Universal Deposit Address, needs a Lighter builder key) are a TODO at `/api/deposit-address`.
