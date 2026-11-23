#!/usr/bin/env python3
"""Sort every company in all_companies.csv into one of ~30 startup / brand categories.

  python report/categorize.py report/all_companies.csv report/categorized.csv
Each category has NIC prefixes (business activity) and name keywords. A company needs a keyword hit,
or a strong NIC with a brand-style name, to qualify; it is placed in its best-scoring category.
"""
import csv, re, sys
from collections import Counter

# id, label, group, [(nic prefix, pts)], keywords (regex alternation, matched at word start), needs_kw
CATS = [
 ("ecommerce", "E-commerce & D2C", "Consumer", [("4791", 4)],
  r"brands?|consumer|d2c|dtc|commerce|ecom|e.?com|shop\b|shoppe|store\b|stores\b|kart|cart\b|bazaar|market\b|mart\b|retail tech|essentials|lifestyle|originals|goods\b", False),
 ("beauty", "Beauty & personal care", "Consumer", [("2023", 4), ("47722", 3), ("9602", 1)],
  r"beauty|skin|cosmetic|glow|derma|haircare|hair care|nails?\b|fragrance|perfume|grooming|makeup|lipstick|serum|spa\b", False),
 ("fashion", "Fashion & apparel", "Consumer", [("141", 3), ("143", 3), ("4771", 2), ("47911", 1)],
  r"fashion|apparel|clothing|couture|wear\b|wears\b|denim|streetwear|ethnic|kurti|saree|lingerie|threads\b|wardrobe", False),
 ("footwear", "Footwear & bags", "Consumer", [("152", 4), ("1512", 4), ("47723", 2)],
  r"footwear|shoes?\b|sneaker|bags?\b|leather|luggage|backpack", False),
 ("jewellery", "Jewellery & accessories", "Consumer", [("3211", 3), ("3212", 4), ("47733", 2), ("32120", 4)],
  r"jewel(?!lers)|jewellery|jewelry|silver\b|gems?\b|ornament|accessor|watches|eyewear|sunglass", False),
 ("decor", "Home & decor", "Consumer", [("1392", 3), ("13930", 3), ("3100", 2), ("47530", 2), ("47591", 2), ("47592", 2), ("2393", 3), ("1629", 2), ("2740", 3)],
  r"decor|furnish|interiors?\b|rugs?\b|carpets?|lighting|lamps?\b|candles?|homeware|home ?store|living\b|furniture|kitchenware|linen", False),
 ("craft", "Handicraft & gifting", "Consumer", [("47735", 4), ("3290", 2), ("32909", 2), ("13", 1)],
  r"handicraft|handloom|crafts?\b|artisan|handmade|pottery|ceramic|gifting|gifts?\b|hampers?|khadi|weaves?\b|terracotta", False),
 ("food", "Food & snacks", "Consumer", [("1071", 3), ("1072", 2), ("1073", 4), ("1074", 2), ("1075", 3), ("1079", 3), ("10504", 3), ("10505", 3), ("1030", 2), ("1061", 1), ("4721", 1)],
  r"foods?\b|snacks?|bakery|bakers|baked|chocolat|sweets|spices|masala|pickles?|millets?|makhana|granola|cookies|nuts\b|treats|kitchens?\b|gourmet|sauces?|ice ?cream|gelato|dessert", False),
 ("drinks", "Drinks (non-alcoholic)", "Consumer", [("1104", 3), ("1107", 2), ("10791", 2), ("10792", 2)],
  r"beverages?|drinks?\b|juices?|teas?\b|chai\b|coffee|kombucha|sodas?\b|tonic|hydration|water\b|matcha|brew\b|brews\b", False),
 ("alcohol", "Spirits, beer & wine", "Consumer", [("1101", 5), ("1102", 5), ("1103", 5)],
  r"spirits|distill|brewing|brewery|breweries|brewers|wines?\b|winery|vineyards?|cellars|liquors?|whisk|gin\b|rum\b|vodka|craft beer|beer", False),
 ("restaurants", "Restaurants & cafés", "Consumer", [("561", 3), ("563", 3)],
  r"cafe|café|restaurant|bistro|eatery|eateries|diner|dine\b|cloud kitchen|qsr|pizza|burger|biryani|bar\b|lounge|tavern|patisserie", False),
 ("nutrition", "Health & nutrition", "Consumer", [("10750", 3), ("10799", 1), ("21004", 1)],
  r"nutrition|nutri|protein|supplement|nutra|vitamin|wellness|ayur|herbal|organics?\b|superfood|gut\b|keto|vegan|plant.?based|probiotic", True),
 ("pets", "Pet care", "Consumer", [("10803", 4), ("10809", 3), ("47734", 4), ("750", 3)],
  r"pets?\b|petcare|paws?\b|dogs?\b|cats?\b|canine|feline|vet\b|veterinary|furry|kitty|pup", False),
 ("kids", "Kids, baby & toys", "Consumer", [("324", 4), ("47640", 3), ("14101", 0)],
  r"kids|baby|babies|toys?\b|junior|tots?\b|mom\b|moms\b|mama|toddler|infant|little ones|playschool|childcare", False),
 ("fitness", "Sports & fitness", "Consumer", [("32300", 4), ("9311", 3), ("9312", 3), ("9319", 2), ("85410", 3), ("47634", 3)],
  r"fitness|gym\b|gyms\b|sports?\b|athlet|yoga|padel|pickleball|cricket|football|futsal|turf|running|cycling|martial|boxing|pilates|tennis|badminton|golf|swim", False),
 ("travel", "Travel & experiences", "Consumer", [("7911", 2), ("7912", 2), ("7990", 2), ("5510", 1), ("5520", 2)],
  r"travel|trips?\b|holidays|journeys?|stays?\b|camps?\b|adventure|experiences|expeditions?|backpack|hostels?|homestays?|retreats?|getaways?|voyage|wander", True),
 ("gadgets", "Gadgets & electronics", "Consumer", [("264", 4), ("2640", 4), ("4741", 2), ("47411", 2), ("4742", 2), ("2630", 2)],
  r"gadgets?|electronics|audio|headphone|speakers?|wearables?|smart home|gear\b|devices?\b|iot\b", False),
 ("ev", "EV & mobility", "Consumer", [("2910", 3), ("2930", 3), ("3091", 4), ("3092", 3), ("2720", 3), ("2790", 2), ("4540", 1)],
  r"ev\b|evs\b|electric|e.?mobility|mobility|motors?\b|scooters?|e.?bikes?|bikes?\b|batter|charging|charge\b|cycles?\b|autonomous", False),
 ("media", "Media, music & creators", "Consumer", [("591", 3), ("592", 4), ("601", 2), ("602", 2), ("9000", 2), ("7420", 2)],
  r"media|studios?\b|films?\b|pictures|content|creators?|entertainment|music|podcast|productions?|animation|vfx|comics?|records\b|cinema|stories", True),
 ("gaming", "Gaming & esports", "Tech", [("5820", 1), ("9200", 2), ("32401", 2)],
  r"games?\b|gaming|esports?|e.?sports|play\b|plays\b|arcade|quest\b|pixel", True),
 ("ai", "AI", "Tech", [("62", 3), ("631", 3), ("72", 2), ("582", 3)],
  r"ai\b|a\.i\.|artificial intelligence|intelligen|machine learning|ml\b|neural|cognitive|gpt|llm|agentic|agents?\b|genai|deep ?learning|vision\b", True),
 ("software", "Software & SaaS", "Tech", [("62", 3), ("631", 3), ("582", 3)],
  r"tech\b|techs\b|technolog|software|labs?\b|cloud|saas|digital|systems|apps?\b|platforms?|infotech|data\b|analytics|code|dev\b|stack|automation|bytes?\b|logic", True),
 ("fintech", "Fintech", "Tech", [("6419", 2), ("6492", 2), ("6499", 2), ("6619", 3), ("6612", 2), ("6622", 1), ("62", 1)],
  r"fintech|fin\b|finserv|pay\b|pays\b|payments?|money|wealth|credit|lend|loans?\b|invest|wallet|insur|neo.?bank|trading tech|capital tech", True),
 ("health", "Healthtech & medtech", "Tech", [("3250", 4), ("86", 2), ("2660", 4), ("62", 1), ("72", 1)],
  r"health|medtech|medi|clinic|diagnost|cure|dental|derm|eye\b|vision care|cardio|neuro|surgical|care\b|healthcare tech|hospital tech|therap|mental|fertility|ortho", True),
 ("biotech", "Biotech & life sciences R&D", "Tech", [("72", 3), ("21", 1), ("7210", 4)],
  r"bio|genom|genetic|therapeut|cell\b|cells\b|molecular|pharma tech|vaccin|enzyme|ferment|protein|synth|lifescience|life science|algae|myco", True),
 ("edtech", "Edtech & learning", "Tech", [("85", 2), ("8550", 3), ("62", 1)],
  r"edtech|learn|edu|academy|academi|skills?\b|tutor|classes|courses?|coaching|mentor|upskill|campus|school of|knowledge", True),
 ("climate", "Climate & clean energy", "Tech", [("35", 2), ("3510", 2), ("38", 2), ("2711", 1), ("2610", 0)],
  r"solar|renewable|green\b|greens\b|climate|carbon|recycl|waste|clean ?tech|cleantech|hydrogen|sustainab|circular|wind\b|energy|eco\b|bio.?fuel|compost|net.?zero", True),
 ("agritech", "Agritech", "Tech", [("01", 1), ("0161", 2), ("62", 1), ("72", 1)],
  r"agritech|agri.?tech|farm.?tech|hydroponic|aquaponic|vertical farm|precision farm|agri.?ai|krishi.?tech|agrotech|agro.?tech|smart farm|microgreens", True),
 ("deeptech", "Deeptech: robotics, drones, space", "Tech", [("2651", 3), ("2610", 3), ("3030", 4), ("2826", 2), ("2829", 1), ("3040", 3), ("72", 2)],
  r"robot|drones?|uav|space\b|aerospace|aero\b|satellite|semiconductor|chips?\b|quantum|defen[cs]e tech|3d print|lidar|photonics|rocket|orbit|nano|avionics|autonom", True),
 ("logistics", "Logistics & delivery tech", "Tech", [("5320", 3), ("5229", 1), ("4923", 0), ("62", 1)],
  r"delivery|deliveries|quick commerce|q.?commerce|last.?mile|fleet|freight tech|shipping tech|dispatch|courier|logistics tech|logitech|movers tech|dunzo", True),
 ("web3", "Web3 & blockchain", "Tech", [("62", 2), ("631", 2), ("6619", 1)],
  r"blockchain|crypto|web3|web 3|defi|nft|token|ledger\b|blockchain|bitcoin|ethereum|metaverse", True),
 ("hrtech", "HR & work tech", "Tech", [("78", 1), ("62", 1)],
  r"hire|hiring|talent|workforce|jobs\b|recruit|gig\b|freelanc|payroll|hr.?tech|careers?\b|workspace|cowork", True),
 ("proptech", "Proptech & home services", "Tech", [("6820", 1), ("62", 1), ("8130", 1), ("9520", 1)],
  r"proptech|prop.?tech|home services|homeservices|rentals?\b|co.?living|coliving|stays tech|property tech|smart living|housing tech|nestaway|repairs?\b|maintenance tech", True),
]
TRAD = re.compile(r"\b(trader|trading|traders|exports?|imports?|impex|exim|overseas|industries|industry|mills|enterprises|infra|infrastructure|realty|builders|developers|wholesale|distributors?|distribution|polymers|chemicals|textiles?|garments?|processors|packaging|plastics|steel|marine|feeds?|oils?\b|refinery|logistics(?! tech)|engineering|lifesciences|pharmaceuticals?|jewellers|diamonds|bullion|properties|estate|construction|contractors?|consultancy|consultants|associates|nidhi|chit|finvest|fincorp|holdings|manpower|security services|transport|travels|tours|hotels?|agro|agri\b|dairy|farmers?|producer|cotton|spinning|cement|granite|marbles|minerals|mining|petroleum|motors and|automobiles|hospitality|welfare|foundation|infratech|infraprojects)\b")
BRANDY = re.compile(r"\b(brands?|consumer|d2c|dtc|labs?|studio|collective|co\b|company|club|house|society|originals|works|world|tribe|nation|culture|hq|the )\b")
CAT_MIN = {"software": 9, "ai": 8, "climate": 7, "health": 7, "edtech": 7, "ecommerce": 8}
HUBS = {"KA", "MH", "DL", "DC", "HR", "TG", "TS", "TN", "TZ", "UP", "GJ", "WB", "KL", "RJ", "PN", "MR", "WR", "UW"}
COMPILED = [(cid, lab, grp, nics, re.compile(r"(?:^|[\s&.(-])(?:" + kw + ")"), need) for cid, lab, grp, nics, kw, need in CATS]


def norm(n):
    n = re.sub(r"\b(private limited|pvt\.? ltd\.?|opc|limited|\(opc\)|\(india\)|india)\b", " ", n.lower())
    return " " + re.sub(r"\s+", " ", n).strip() + " "


def best(cin, name, nic, state, cls):
    n = norm(name); trad = TRAD.search(n)
    results = []
    for cid, lab, grp, nics, kw, need in COMPILED:
        npts = max([p for pre, p in nics if nic.startswith(pre)], default=None)
        k = kw.search(n)
        if not k and (need or npts is None):
            continue
        if not k and npts < 3:
            continue
        s, why = 0, []
        if npts is not None:
            s += npts; why.append(f"NIC {nic} +{npts}")
        if k:
            s += 3; why.append(f"'{k.group(0).strip(' &.(-')}' in name +3")
        if BRANDY.search(n):
            s += 1; why.append("brand-style name +1")
        if trad:
            s -= 4; why.append(f"'{trad.group(0)}' in name -4")
        if cls == "PTC":
            s += 1; why.append("Pvt Ltd +1")
        if state in HUBS:
            s += 1; why.append("hub state +1")
        results.append((s, cid, lab, grp, "; ".join(why)))
    return max(results) if results else None


def main():
    src, out, minsc = sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 6
    rows = []
    for r in csv.DictReader(open(src)):
        b = best(r["cin"], r["name"], r["nic"], r["state_code"], r["cls"])
        if b and b[0] >= max(minsc, CAT_MIN.get(b[1], 0)):
            s, cid, lab, grp, why = b
            rows.append({**r, "score": s, "cat": cid, "cat_label": lab, "group": grp, "why": why})
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    c = Counter(r["cat_label"] for r in rows)
    print(len(rows), "companies")
    for k, v in c.most_common(): print(f"  {v:6}  {k}")


if __name__ == "__main__":
    main()
