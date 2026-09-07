"""Everything tunable in one place."""

# ---------------------------------------------------------------- base control map
# Kalibrated publishes as a Google My Maps; My Maps exposes KML at these URLs without a login.
KALIBRATED_MID = "19JQzYZ3O2zC02_zj36ynO4n1So5x0UE"
KALIBRATED_URLS = [
    "https://www.google.com/maps/d/kml?mid={mid}&forcekml=1",   # plain KML, folders preserved
    "https://www.google.com/maps/d/u/0/kml?mid={mid}&forcekml=1",
    "https://www.google.com/maps/d/kml?mid={mid}",              # KMZ fallback
]

# Russian-side control map. "Карта СК" by @creamy_caprice, the map LostArmour's daily сводка is drawn against.
# Same Google My Maps mechanism as Kalibrated, so the same parser and the same classification apply.
RU_MYMAPS = {
    "creamy_caprice": "190QzpVjSlsrUkTn9iooNr6Il2ZYmGBM",
}

# Any other My Maps mapper. Add "name": "mid" and it is fetched and classified the same way.
EXTRA_MYMAPS = {
    # "hudson": "<mid>",
}

# LostArmour daily situation report: structured by axis, names settlements in Russian, no login.
LOSTARMOUR_SUMMARY = "https://lostarmour.info/summary/voyna_na_ukraine-svodka-za-{d}"   # d = DD-MM-YYYY
LOSTARMOUR_DAYS = 10

# ---------------------------------------------------------------- ISW (comparison + claims tab)
ISW_ITEMS = ["2dda62a957ce4cc69048f9e886611d55", "7360c66e2b1b4b9e994fbd1edf5ae417"]
ISW_WANT = {
    "claimedrussianterritory": "claimed",
    "claimed russian": "claimed",
    "russiacotinukraine": "control",
    "russia cot": "control",
    "assessed russian controlled": "control",
    "assessed control": "control",
    "russian controlled ukrainian territory": "control",
    "assessedrussianadvance": "advances",
    "assessed russian advance": "advances",
    "russian advances": "advances",
    "fortification": "fortifications",
    "partisan": "partisan",
}

# ---------------------------------------------------------------- text sources
# Russian side. Public web preview at t.me/s/<name>, no login.
RU_CHANNELS = ["rybar", "dva_majors", "voenkorKotenok", "RVvoenkor", "wargonzo", "boris_rozhin", "mod_russia"]
# Ukrainian side, deliberately not a mapping project: official General Staff plus front-line commentators.
UA_CHANNELS = ["GeneralStaffZSU", "butusov_plus", "mashovets_kostyantyn", "zloyodessit"]
# Mapper commentary that carries settlement names in text (their maps stay images).
MAPPER_CHANNELS = ["kalibrated", "suriyakUA"]

TELEGRAM_DAYS = 14          # how far back each run reads
EVENT_KEEP_DAYS = 60        # how long parsed events stay in the file

# ---------------------------------------------------------------- classification of KML layers
# Matched against the lowercased layer name first, then the fill colour.
# Layers that are never control, whatever colour they are: map keys, archives of past events, notes.
IGNORE_LAYER_KEYWORDS = [
    "условные обозначения", "обозначения", "legend", "key", "архив", "archive", "notes", "untitled",
    "год", "20", "история", "history", "past", "old",
]

LAYER_KEYWORDS = [
    ("ru_control",  ["russian control", "russian-controlled", "russian occupied", "occupied", "russian advance zone", "under russian"]),
    ("ua_control",  ["ukrainian control", "ukrainian-controlled", "ukrainian held", "afu control", "liberated", "ukrainian advance"]),
    ("grey",        ["contested", "grey", "gray", "unclear", "disputed", "no man", "buffer"]),
    ("ru_claimed",  ["claimed", "reported", "unconfirmed"]),
    ("advance",     ["advance", "axis", "attack", "push", "thrust", "direction"]),
    ("infiltration",["infiltration", "drg", "sabotage", "recon"]),
    ("fortification",["fortification", "defensive line", "defence line", "defense line", "trench"]),
    ("strike",      ["strike", "hit", "target", "explosion"]),
    ("unit",        ["unit", "brigade", "regiment", "formation"]),
]
# Fallback by fill colour family when the layer name says nothing.
COLOUR_CLASS = {"red": "ru_control", "crimson": "ru_control", "pink": "ru_claimed",
                "blue": "ua_control", "cyan": "ua_control", "grey": "grey", "yellow": "grey", "orange": "advance"}

# ---------------------------------------------------------------- geometry / rendering
BBOX = (20.5, 43.0, 42.5, 53.5)
UKRAINE_KM2 = 603628
MIN_POLY_KM2 = 0.05          # drop slivers below this
MAX_SOURCE_KM2 = 300_000     # after clipping to Ukraine, more than this is implausible; drop it and say so
UNCERTAINTY_MIN_KM = 2.0     # feathered edge, minimum half-width
UNCERTAINTY_MAX_KM = 12.0    # feathered edge where sources disagree most
EVENT_CLUSTER_KM = 12.0      # radius for turning report clusters into an uncertainty zone
HISTORY_KEEP_DAYS = 800
