import json, hashlib, re
obd = json.load(open('obd.json'))['order_book_details']
stats = {o['symbol']: o for o in json.load(open('stats.json'))['order_book_stats']}
perps = [o for o in obd if o['market_type'] == 'perp' and o['status'] == 'active']

ZONE = {}
def put(z, syms):
    for s in syms.split(): ZONE[s] = z
put('reef', 'BTC BNB XRP ADA AVAX LTC BCH TRX XLM HBAR DOT LINK TON ICP FIL XMR ZEC DASH CRO QNT MNT POL ATOM')
put('bay', 'ETH ARB OP STRK ZK LINEA SOL SUI APT NEAR SEI TIA S BERA MON MEGA ETHFI EIGEN LDO ENA AAVE UNI CRV MORPHO PENDLE SKY SYRUP ONDO AERO RAY JUP JTO PYTH ZRO WLD DYDX GMX APEX AXS 0G 2Z ZORA PROVE KAITO EDGE AZTEC STABLE STBL PLUME XPL FF FOLKS AVNT MET RIVER BB CC DRV LIT HYPE ASTER WLFI VARIATIONAL PRL')
put('bayou', 'DOGE 1000PEPE WIF 1000BONK 1000SHIB 1000FLOKI 1000NOT FARTCOIN POPCAT PENGU TRUMP WEN USELESS CASHCAT ANSEM PUMP BOT SKR GRAM')
put('pier', 'AAPL NVDA TSLA MSFT AMZN GOOGL META AMD INTC COIN MSTR PLTR HOOD GME SPY QQQ US100 US500 IWM SOXL SOXS AVGO TSM ASML ARM ORCL IBM QCOM MU WDC SNDK DELL MRVL CRCL CRWV NBIS RKLB BABA BYD TENCENT XIAOMI SAMSUNGUSD SKHYNIXUSD SKHY HYUNDAIUSD KIOXIA SMIC CXMT DRAM CHIP NOK NOW MRNA GEV BE BMNR AAOI AXTI LITE TTWO POPMART SHEIN UNITREE OURA KORU EWY CBRS SPCX SPX STRC STONK RAIL ROBO DATA CAP PONS CTR ARC BIO US10Y')
put('gold', 'XAU XAG XCU XPT XPD PAXG WTI BRENTOIL NATGAS EURUSD GBPUSD USDJPY AUDUSD NZDUSD USDCAD USDCHF USDHKD USDKRW STABLECOINX')
put('abyss', 'TAO VIRTUAL AI OPENAI ANTHROPIC H100 GRASS ZHIPU MINIMAX VVV')
put('lagoon', 'HYPE LIT ASTER APEX DRV VARIATIONAL')

SPECIES = ['tuna','swordfish','puffer','catfish','koi','shark','eel','angler','clown','blobfish','sunfish','piranha','jelly','crab','octopus']
# hand-made: symbol -> (species, silly name, colour, quip)
H = {
 'BTC': ('swordfish','Sir Satoshi Swordfish','#f7931a','The OG. Pointy. Unbothered.'),
 'ETH': ('tuna','Ultrasound Tuna','#7b7bff','Makes a noise only bats can hear.'),
 'SOL': ('piranha','Turbo Sol-mon','#19e6a0','Goes fast, sometimes stops.'),
 'HYPE': ('koi','Hype Koi Carp-tain','#5fe0d4','Captain of the hype cycle.'),
 'LIT': ('angler','Lighter Lanternfish','#ffd23f','The house fish. Brings its own light.'),
 'DOGE': ('puffer','Much Wow Puffer','#e8c547','Such inflate. Very spike.'),
 '1000PEPE': ('blobfish','Pepe the Frogfish','#5cc24b','Feels good, man.'),
 'WIF': ('catfish','Dog Wif Fish','#c49a6c','Is a cat. Is a fish. Wears a hat.'),
 '1000BONK': ('piranha','Bonk Piranha','#ff9f1c','Bonks first, asks later.'),
 '1000SHIB': ('shark','Shiba Sharku','#ff8c42','Very bark, many teeth.'),
 '1000FLOKI': ('eel','Floki the Viking Eel','#d4a373','Raids your margin at dawn.'),
 '1000NOT': ('jelly','Not a Jellyfish','#9b8cff','It is, though.'),
 'FARTCOIN': ('puffer','Toot Puffer','#a7c957','Propelled by questionable gas.'),
 'POPCAT': ('catfish','Pop Catfish','#ff6b9d','Opens mouth. Pops.'),
 'PENGU': ('sunfish','Waddle Sunfish','#8ecae6','Waddles. Underwater. Somehow.'),
 'TRUMP': ('sunfish','Tremendous Sunfish','#ff4d4d','Many people say it is the best fish.'),
 'WEN': ('jelly','Wen Lambo Jelly','#ffb4a2','Wen? Soon.'),
 'USELESS': ('blobfish','Truly Useless Blobfish','#e0aaff','Does nothing. Proud of it.'),
 'CASHCAT': ('catfish','Cash Catfish','#2ec4b6','Purrs in dollars.'),
 'ANSEM': ('angler','Ansem the Caller','#9d4edd','Calls the top. Calls the bottom. Calls you.'),
 'PUMP': ('puffer','Pump Puffer','#ff5ca8','Keeps inflating. Do not poke.'),
 'XRP': ('eel','Ripple Eel','#23292f','Makes little ripples. Wins lawsuits.'),
 'BNB': ('koi','Big Bnb Bream','#f3ba2f','Sits in the middle of every pond.'),
 'ADA': ('jelly','Cardano Peer-Reviewed Jelly','#3468d1','Will swim after the paper is published.'),
 'AVAX': ('crab','Avalanche Crab','#e84142','Snow on the claws.'),
 'LINK': ('eel','Chainlink Eel','#2a5ada','Links everything to everything.'),
 'DOT': ('clown','Polka-Dot Clownfish','#e6007a','Dots. So many dots.'),
 'LTC': ('tuna','Lite Tuna','#a6a9aa','Same fish, half the calories.'),
 'BCH': ('tuna','Bitcoin Cash Bass','#8dc351','The bigger-block cousin.'),
 'TRX': ('octopus','Tron Octopus','#ff0013','Eight arms, all sending stablecoins.'),
 'XLM': ('jelly','Stellar Starjelly','#14b6e7','Glows a little.'),
 'HBAR': ('crab','H-Bar Hermit Crab','#3e3e3e','Lives in a council-governed shell.'),
 'SUI': ('sunfish','Sui-t Sunfish','#4da2ff','Wears a suit. Very serious.'),
 'NEAR': ('eel','Near-Shore Eel','#00ec97','Always close. Never quite there.'),
 'APT': ('koi','Aptos Koi','#06f7f7','Move-language native.'),
 'ARB': ('tuna','Arbi-Tuna','#28a0f0','Rolls up, rolls down.'),
 'OP': ('clown','Optimistic Clownfish','#ff0420','Sees only green candles.'),
 'TIA': ('jelly','Celestia Jelly','#7b2bf9','Modular. Comes in pieces.'),
 'SEI': ('shark','Sei Shark','#9e1f19','Very fast parallel bites.'),
 'AAVE': ('angler','Aave Ghost Angler','#b6509e','Lends you its lantern, with interest.'),
 'UNI': ('sunfish','Uni-Corn Sunfish','#ff007a','Has a horn. Swaps anything.'),
 'CRV': ('eel','Curve Eel','#ff4e4e','Extremely curvy.'),
 'LDO': ('octopus','Lido Octopus','#00a3ff','Stakes with all eight arms.'),
 'PENDLE': ('clown','Pendle Pendulum Fish','#25cdd6','Swings between yield and fixed.'),
 'JUP': ('sunfish','Jupiter Jumper','#c7f284','Jumps aggregator to aggregator.'),
 'GMX': ('shark','GMX Hammerhead','#2d42fc','Hammers liquidity.'),
 'DYDX': ('swordfish','dYdX Dart','#6966ff','Derivative of a swordfish.'),
 'ENA': ('jelly','Ethena Yield Jelly','#111111','Pays you to float.'),
 'ETHFI': ('tuna','Ether.fi Fry','#6e5bff','Restaked twice. Dizzy.'),
 'MORPHO': ('koi','Morpho Butterflyfish','#2470ff','Pretty. Lends things.'),
 'ONDO': ('crab','Ondo Treasury Crab','#0a2540','Hides T-bills in its shell.'),
 'SKY': ('sunfish','Sky-Diving Sunfish','#1aab9b','Used to be called something else.'),
 'ASTER': ('jelly','Aster Starjelly','#f5b301','Pointy stars, soft middle.'),
 'ZEC': ('eel','Zcash Ghost Eel','#f4b728','You cannot see it. That is the point.'),
 'XMR': ('angler','Monero Mystery Angler','#ff6600','Lantern on. Face hidden.'),
 'TAO': ('octopus','Bittensor Brain-topus','#cccccc','Nine brains. Overthinks.'),
 'VIRTUAL': ('jelly','Virtual Jellyfish','#3cf2ff','Not real. Probably.'),
 'AI': ('octopus','Artificial Octopus','#a29bfe','Trained on fish memes.'),
 'OPENAI': ('shark','Great White GPT','#10a37f','Predicts your next bite.'),
 'ANTHROPIC': ('crab','Claw-d Crab','#d97757','Helpful, harmless, pinchy.'),
 'H100': ('piranha','H100 GPU Piranha','#76b900','Eats electricity.'),
 'NVDA': ('shark','Nvidia GPU Shark','#76b900','Sells shovels to other sharks.'),
 'TSLA': ('eel','Tesla Electric Eel','#cc0000','Self-swimming. Mostly.'),
 'AAPL': ('koi','Apple Koi','#a2aaad','Sleek. Overpriced. Beloved.'),
 'MSFT': ('tuna','Window Tuna','#00a4ef','Comes with a free paperclip.'),
 'AMZN': ('piranha','Amazon Piranha','#ff9900','Delivers bites in one day.'),
 'GOOGL': ('clown','Googly-Eyed Clownfish','#4285f4','Has seen your search history.'),
 'META': ('jelly','Metaverse Jellyfish','#0866ff','Has legs now.'),
 'AMD': ('piranha','Red Team Piranha','#ed1c24','Underdog with sharp teeth.'),
 'INTC': ('blobfish','Intel Inside Blobfish','#0071c5','Something is inside. Unclear what.'),
 'COIN': ('crab','Coinbase Crab','#0052ff','Charges a fee to pinch you.'),
 'MSTR': ('swordfish','Saylor Marlin','#ff6b00','Converted its whole body into Bitcoin.'),
 'PLTR': ('angler','Palantir Seeing-Eye Angler','#101010','Already knows what you will cast.'),
 'HOOD': ('catfish','Robinhood Hood-fish','#00c805','Steals from the rich pond.'),
 'GME': ('puffer','GameStop Moon Puffer','#e01d2b','Apes together strong.'),
 'SPY': ('tuna','Index Tuna','#1a5276','Boring. Wins anyway.'),
 'QQQ': ('eel','Triple-Q Tech Eel','#6c3483','Three Qs, one eel.'),
 'US500': ('tuna','Five Hundred Sardines','#2e86c1','A school, wearing a tuna costume.'),
 'US100': ('swordfish','Tech Hundred Marlin','#8e44ad','A hundred swordfish, one sword.'),
 'XAU': ('sunfish','Golden Sunfish','#d4af37','Shiny. Heavy. Boomer-approved.'),
 'XAG': ('koi','Silver Koi','#c0c0c0','Gold\'s little sibling.'),
 'WTI': ('blobfish','Oil Slick Blobfish','#3b2f2f','Slippery. Very slippery.'),
 'BRENTOIL': ('catfish','Brent the Oily Catfish','#5a4632','From the North Sea. Grumpy.'),
 'NATGAS': ('puffer','Gassy Puffer','#58c4dd','Do not light a match.'),
 'XCU': ('crab','Copper Crab','#b87333','Conducts electricity. And itself.'),
 'XPT': ('koi','Platinum Koi','#e5e4e2','Rarer than gold. Quieter.'),
 'PAXG': ('sunfish','Paxos Gold Sunfish','#e4c26b','Gold, but on-chain.'),
 'EURUSD': ('eel','Euro Eel','#003399','Slides between two flags.'),
 'GBPUSD': ('tuna','Cheeky Pound Pike','#cf142b','Drinks tea between bites.'),
 'USDJPY': ('koi','Yen Koi','#bc002d','Carry-trade legend.'),
 'AUDUSD': ('crab','Aussie Crab','#00843d','Swims upside down.'),
 'USDCAD': ('sunfish','Loonie Sunfish','#d52b1e','Apologises after it bites.'),
 'GRASS': ('blobfish','Touch-Grass Blobfish','#22c55e','Has never touched grass.'),
 'ZORA': ('jelly','Zora Coin-Jelly','#3399ff','Every post is a fish.'),
 'WLD': ('angler','Worldcoin Orb-Angler','#000000','Wants to scan your eyeball.'),
 'WLFI': ('sunfish','Liberty Sunfish','#c9a227','Patriotic and shiny.'),
 'BERA': ('blobfish','Bera Blobfish','#814625','Bear, but make it a fish.'),
 'MON': ('koi','Monad Koi','#836ef9','Parallel fins.'),
 'MEGA': ('shark','Mega-lodon','#ff3366','Big. Fast. Real-time.'),
 'BOT': ('octopus','Robo-topus','#9ca3af','Beep boop, glub glub.'),
 'S': ('clown','Sonic the Clownfish','#1e40af','Gotta swim fast.'),
 'ZK': ('angler','Zero-Knowledge Angler','#8c8dfc','Proves it bit you, reveals nothing.'),
 'STRK': ('swordfish','Starknet Starfish-Sword','#ec796b','Cairo-coded fins.'),
 'PYTH': ('eel','Pythia Oracle Eel','#e6dafe','Whispers prices.'),
 'JTO': ('jelly','Jito Tip Jelly','#2bd47f','Tips the validator, then you.'),
 'RAY': ('shark','Raydium Ray','#c200fb','Technically a ray. Close enough.'),
 'PENGU2': ('tuna','x','#fff',''),
}
H.pop('PENGU2')
SP_TPL = {'tuna':'Tuna','swordfish':'Swordfish','puffer':'Puffer','catfish':'Catfish','koi':'Koi','shark':'Shark','eel':'Eel','angler':'Angler','clown':'Clownfish','blobfish':'Blobfish','sunfish':'Sunfish','piranha':'Piranha','jelly':'Jelly','crab':'Crab','octopus':'Octopus'}
TEMPL = ['Lil\' {t} {s}', 'Captain {t} {s}', '{t} McFinface', 'Big {t} Energy {s}', 'Sir {t} the {s}', 'Professor {t} {s}', '{t}-a-saurus {s}', 'Grumpy {t} {s}', 'Sparkly {t} {s}', 'Turbo {t} {s}', 'Sleepy {t} {s}', 'Chonky {t} {s}', 'Wiggly {t} {s}', 'Baron von {t} {s}', 'Disco {t} {s}']
QUIPS = ['Bites when you least expect it.', 'Has strong opinions about candles.', 'Swims in circles. Like the chart.', 'Rumoured to be a whale in disguise.', 'Allergic to stop losses.', 'Only comes out at market open.', 'Loves a good short squeeze.', 'Very shy. Then very not.', 'Eats liquidity for breakfast.', 'Will absolutely wreck your bait.']
ZONE_SP = {'pier': ['tuna','swordfish','koi','shark','catfish','eel'], 'gold': ['sunfish','koi','crab','eel','blobfish'], 'abyss': ['octopus','angler','jelly','eel'], 'bayou': ['puffer','catfish','blobfish','piranha','clown'], 'reef': ['clown','koi','crab','tuna','jelly'], 'bay': ['tuna','eel','jelly','clown','koi','sunfish'], 'lagoon': ['koi','angler','jelly']}
PAL = ['#ff6b6b','#ffa94d','#ffd43b','#69db7c','#38d9a9','#4dabf7','#748ffc','#da77f2','#f783ac','#a9e34b','#3bc9db','#ff8787']
def h(s, n): return int(hashlib.md5(s.encode()).hexdigest(), 16) % n
def pretty(sym):
    t = re.sub(r'^1000', '', sym); t = re.sub(r'USD$', '', t) if len(t) > 6 else t
    return t if len(t) <= 3 or t.isupper() and len(t) <= 5 else t.title()
rows = []
for o in sorted(perps, key=lambda o: -(stats.get(o['symbol'], {}).get('daily_quote_token_volume', 0))):
    s = o['symbol']; z = ZONE.get(s, 'open'); st = stats.get(s, {})
    px = float(o.get('mark_price') or o.get('last_trade_price') or 0)
    if px <= 0: continue
    lev = max(2, min(50, round(10000 / max(1, o['min_initial_margin_fraction']))))
    vol = float(st.get('daily_quote_token_volume', 0))
    if s in H: sp, name, col, quip = H[s]
    else:
        sp = (ZONE_SP.get(z) or SPECIES)[h(s, len(ZONE_SP.get(z) or SPECIES))]
        name = TEMPL[h(s + 'n', len(TEMPL))].format(t=pretty(s), s=SP_TPL[sp]); col = PAL[h(s + 'c', len(PAL))]; quip = QUIPS[h(s + 'q', len(QUIPS))]
    dvol = {'gold': 0.0007, 'pier': 0.0011, 'reef': 0.0018, 'bay': 0.0024, 'lagoon': 0.003, 'abyss': 0.0032, 'bayou': 0.0048, 'open': 0.0025}[z]
    rows.append([s, o['market_id'], float(f'{px:.6g}'), lev, z, sp, name, col, quip, round(vol), o['size_decimals'], o['price_decimals'], dvol])
open('roster.js.txt', 'w').write('  const ROSTER = ' + json.dumps(rows, separators=(',', ':'), ensure_ascii=False) + ';\n')
print(len(rows), sum(1 for r in rows if r[0] in H), 'hand-named')
from collections import Counter; print(Counter(r[4] for r in rows))
for r in rows[:12]: print(r[0], r[6], r[5], r[3], r[2])
print([ (r[0], r[6]) for r in rows if r[0] not in H][:25])
