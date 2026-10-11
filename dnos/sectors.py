"""Classify Street Manager promoter organisations into utility sectors (by name: Street Manager gives no sector)."""
import re

SECTORS = ("electricity", "gas", "water", "telecoms", "highway", "other")
_RULES = [
    ("gas", r"\bgas\b|cadent|\bsgn\b|southern gas|scotland gas|wales and west|wales & west|northern gas|gtc\b|"
            r"energy assets|fulcrum|independent pipelines|last mile gas|indigo pipelines|leep utilities|e ?s pipelines"),
    ("electricity", r"power ?networks|powergrid|power distribution|electricity|ssepd|ssen|\bsse\b|national grid elec|"
                    r"manweb|sp distribution|sp energy|western power|ukpn|electric|\bidno\b|eclipse power|energetics|"
                    r"last mile electricity|harlaxton|vattenfall|murphy power|ics power|forbury|utility assets|"
                    r"national grid(?! gas)|connected kerb|char\.gy|ubitricity|ev charging|chargepoint|zest|"
                    r"pod point|osprey|instavolt|blink charging|evyve|believ|\bgridserve|indigo power|charging solutions|qwello|"
                    r"orsted|east anglia (one|two|three)|distribution connection|vital energi"),
    ("water", r"water|waterworks|severn trent|united utilities|yorkshire water|anglian|thames|wessex|hafren|"
              r"dwr cymru|welsh water|scottish water|affinity|independent water|icosa|veolia|albion|leep networks \(water"),
    ("telecoms", r"\bbt\b|openreach|virgin media|cityfibre|hyperoptic|gigaclear|fibre|fiber|broadband|"
                 r"communications|telecom|telecommunications|networks? ltd|vodafone|\bee\b|three uk|o2|telefonica|"
                 r"colt|zayo|neos|euronet|grain|\bkcom\b|lit fibre|toob|community fibre|g\.network|g network|"
                 r"trooli|netomnia|brsk|youfibre|truespeed|wildanet|airband|full fibre|freedom fibre|ogi|"
                 r"jurassic|lightspeed|vorboss|exponential-e|ccs\b|cellnex|mbnl|cornerstone|zzoomm|swish|"
                 r"upp\b|county broadband|connexin|giganet|ftth|starlink|digital infrastructure|fibrus|hutchison|"
                 r"wireless|eunetworks|kingston comms|interoute|telcom|verizon|alncom|voneus|glide business|comms\b|"
                 r"freshwave|open network systems|internetty|exascale|arqiva|nynet|wildcard|sicom|payphones"),
    ("highway", r"council|borough|county|city of|national highways|transport for london|\btfl\b|highways|"
                r"metropolitan|combined authority|royal borough|corporation of london|city corporation|"
                r"network rail|unitary|transport for greater manchester|hs2|environment agency|dft road"),
]
_COMPILED = [(s, re.compile(p, re.I)) for s, p in _RULES]


def sector(org):
    name = org or ""
    for s, rx in _COMPILED:
        if rx.search(name):
            return s
    return "other"
