"""Grocery macro-categories (English slugs). Assigned at receipt import; sticky per product_key."""

from __future__ import annotations

import re

# --- constants (English slugs; labels for UI / i18n keys) ---

FRUIT = "fruit"
VEGETABLES = "vegetables"
HERBS_AROMATICS = "herbs_aromatics"
MEAT = "meat"
POULTRY = "poultry"
FISH_SEAFOOD = "fish_seafood"
CHEESE = "cheese"
DELI_COLD_CUTS = "deli_cold_cuts"
DAIRY = "dairy"
PLANT_DAIRY = "plant_dairy"
EGGS = "eggs"
BAKERY = "bakery"
PASTA_RICE_GRAINS = "pasta_rice_grains"
BAKING_ESSENTIALS = "baking_essentials"
CANNED_JARS = "canned_jars"
OILS_VINEGARS_CONDIMENTS = "oils_vinegars_condiments"
SPICES_SEASONINGS = "spices_seasonings"
SAVORY_SNACKS = "savory_snacks"
SWEETS_CHOCOLATE = "sweets_chocolate"
BREAKFAST = "breakfast"
FROZEN = "frozen"
READY_MEALS = "ready_meals"
BABY_FOOD = "baby_food"
WATER = "water"
SOFT_DRINKS = "soft_drinks"
JUICES = "juices"
COFFEE_TEA = "coffee_tea"
ALCOHOL = "alcohol"
HOUSEHOLD_CLEANING = "household_cleaning"
PERSONAL_CARE = "personal_care"
PAPER_DISPOSABLES = "paper_disposables"
OTHER = "other"

MACRO_CATEGORIES: tuple[str, ...] = (
    FRUIT,
    VEGETABLES,
    HERBS_AROMATICS,
    MEAT,
    POULTRY,
    FISH_SEAFOOD,
    CHEESE,
    DELI_COLD_CUTS,
    DAIRY,
    PLANT_DAIRY,
    EGGS,
    BAKERY,
    PASTA_RICE_GRAINS,
    BAKING_ESSENTIALS,
    CANNED_JARS,
    OILS_VINEGARS_CONDIMENTS,
    SPICES_SEASONINGS,
    SAVORY_SNACKS,
    SWEETS_CHOCOLATE,
    BREAKFAST,
    FROZEN,
    READY_MEALS,
    BABY_FOOD,
    WATER,
    SOFT_DRINKS,
    JUICES,
    COFFEE_TEA,
    ALCOHOL,
    HOUSEHOLD_CLEANING,
    PERSONAL_CARE,
    PAPER_DISPOSABLES,
    OTHER,
)

MACRO_LABELS: dict[str, str] = {
    FRUIT: "Fruit",
    VEGETABLES: "Vegetables",
    HERBS_AROMATICS: "Herbs & aromatics",
    MEAT: "Meat",
    POULTRY: "Poultry",
    FISH_SEAFOOD: "Fish & seafood",
    CHEESE: "Cheese",
    DELI_COLD_CUTS: "Deli & cold cuts",
    DAIRY: "Milk & dairy",
    PLANT_DAIRY: "Plant-based dairy",
    EGGS: "Eggs",
    BAKERY: "Bread & bakery",
    PASTA_RICE_GRAINS: "Pasta, rice & grains",
    BAKING_ESSENTIALS: "Baking essentials",
    CANNED_JARS: "Canned & jarred",
    OILS_VINEGARS_CONDIMENTS: "Oils, vinegars & condiments",
    SPICES_SEASONINGS: "Spices & seasonings",
    SAVORY_SNACKS: "Savory snacks",
    SWEETS_CHOCOLATE: "Sweets & chocolate",
    BREAKFAST: "Breakfast",
    FROZEN: "Frozen",
    READY_MEALS: "Ready meals",
    BABY_FOOD: "Baby food",
    WATER: "Water",
    SOFT_DRINKS: "Soft drinks",
    JUICES: "Juices",
    COFFEE_TEA: "Coffee & tea",
    ALCOHOL: "Alcohol",
    HOUSEHOLD_CLEANING: "Household & cleaning",
    PERSONAL_CARE: "Personal care",
    PAPER_DISPOSABLES: "Paper & disposables",
    OTHER: "Other",
}

_VALID = frozenset(MACRO_CATEGORIES)

# (category_slug, keyword_phrases) — first match wins; order matters.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (HOUSEHOLD_CLEANING, (
        "shopping bag", "plast shopping", "delivery fee", "service fee", "detergent",
        "omo", "comfort", "bleach", "clrx", "stain rem", "wash-up", "airfryer",
        "hmd pulse", "elfbar", "film", "fujifilm",
    )),
    (PERSONAL_CARE, (
        "toothbrush", "t/b", "t/brush", "toothpaste", "t/p", "colgate tb", "shampoo",
        "shmp", "deodorant", "rexona", "dove ro", "scholl", "wax", "trisa", "streax",
        "just for men", "hair color", "rstr", "odor", "swim short",
    )),
    (PAPER_DISPOSABLES, ("cling", "diamond cling", "toilet paper", "kitchen roll", "tissue")),
    (ALCOHOL, (
        "heineken", "beer", "wine", "whisky", "whiskey", "vodka", "champagne",
        "prosecco", "cider", "gin tonic", "rum ", " rum", "spirit",
    )),
    (WATER, (
        "still water", "sparkling water", "mineral water", "masafi", "aquafina",
        "oasis blu", "san pel", "pel.", "perrier", "pellegrino", "acqua blu",
        "mai dubai",
    )),
    (SOFT_DRINKS, (
        "cola", "coca", "coke", "sprite", "fanta", "pepsi", "7up", "soda",
        "soft drink", "zero sugar", "energy drink", "red bull",
    )),
    (JUICES, ("juice", "nectar", "smoothie")),
    (COFFEE_TEA, ("nescafe", "coffee", "espresso", "cappuccino", "tea bag", " green tea", " black tea")),
    (BABY_FOOD, ("baby food", "infant formula", "follow-on", "omogeneiz", "baby puree")),
    (EGGS, ("egg", " uova", "eggs")),
    (CHEESE, (
        "cheese", " brie", "brie ", " bri ", " bri", "gorgonzola", " gorg ", "mascarpone", "mozzarella",
        "feta", "cheddar", "parmesan", "parmigiano", "ricotta", "camembert", "gruyere",
        "emmental", "halloumi", "cottage cheese", "cream cheese", "philadelphia",
        "ciliegine", "burrata",
    )),
    (DELI_COLD_CUTS, (
        "salami", "prosciutto", "ham ", " mortadella", "bresaola", "pepperoni",
        "cold cut", "deli meat", "turkey slice", "pastrami",
    )),
    (FISH_SEAFOOD, (
        "salmon", "tuna", "shrimp", "prawn", "seafood", "fish ", " fish", "cod ",
        "sardine", "anchov", "calamari", "squid", "mussel", "crab", "lobster",
    )),
    (POULTRY, ("chicken", "turkey", "duck ", "poultry", "drumstick", "wing ")),
    (MEAT, (
        "beef", "steak", "stea ", "lamb", "veal", "mince", "burger patty", "ribeye",
        "sirloin", "pork", "sausage", " merguez", "kebab meat",
    )),
    (PLANT_DAIRY, (
        "almond milk", "soy milk", "oat milk", "coconut drink", "plant milk",
        "vegan cheese", "vegan yogurt",
    )),
    (DAIRY, ("milk", "yogurt", "yoghurt", "butter", "cream ", "kefir", "labneh", "ghee")),
    (HERBS_AROMATICS, ("basil", "parsley", "mint ", "coriander bunch", "dill ", "rosemary", "thyme")),
    (FRUIT, (
        "apple", "banana", "orange", "lemon", "lime", "grape", "berry", "mango",
        "melon", "watermelon", "pineapple", "avocado", "pear ", "peach", "plum",
        "strawberr", "blueberr", "raspberr", "kiwi", "dates ", "fig ",
    )),
    (VEGETABLES, (
        "tomato", "potato", "onion", "carrot", "cucumber", "lettuce", "salad",
        "pepper", "broccoli", "spinach", "cabbage", "eggplant", "zucchini",
        "cauliflower", "mushroom", "garlic", "ginger root", "celery",
    )),
    (FROZEN, ("frozen", "surgel", "ice cream", "gelato")),
    (READY_MEALS, (
        "ready meal", "meal kit", "big mac", "mcnugget", "shawarma", "burrito",
        "sandwich", "pizza slice", "lasagne", "biryani",
    )),
    (BAKERY, (
        "bread", "pita", "croissant", "bagel", "muffin", "brioche", "focaccia",
        "crackers", "water cracker", "flatbread", "tortilla wrap",
    )),
    (PASTA_RICE_GRAINS, (
        "pasta", "spaghetti", "penne", "fusilli", "rice ", " basmati", "quinoa",
        "couscous", "bulgur", "noodle", "vermicelli",
    )),
    (BAKING_ESSENTIALS, (
        "flour", "baking powder", "yeast", "icing sugar", "caster sugar", "cocoa powder",
    )),
    (CANNED_JARS, (
        "canned", " tinned", "in brine", "in oil", "hummus", "tahini", "jam ",
        " marmalade", "peanut butter", "nutella",
    )),
    (OILS_VINEGARS_CONDIMENTS, (
        "olive oil", "sunflower oil", "vegetable oil", "vinegar", "ketchup",
        "mayonnaise", "mustard", "soy sauce", "pasta sauce", "tomato sauce",
        "salsa", "bbq sauce", "hot sauce", "dressing",
    )),
    (SPICES_SEASONINGS, (
        "spice", "curry", "cumin", "paprika", "turmeric", "peppercorn", "zaatar",
        "stock cube", "bouillon", "seasoning", "salt ", " sea salt",
    )),
    (SAVORY_SNACKS, (
        "chips", "crisps", "pringles", "popcorn", "nachos", "pretzel", "snack mix",
    )),
    (SWEETS_CHOCOLATE, (
        "chocolate", "candy", "biscuit", "cookie", "wafer", "lindt", "kinder",
        "haribo", "gummy", "cake ", "brownie", "donut", "doughnut",
    )),
    (BREAKFAST, (
        "cereal", "muesli", "granola", "oats ", "porridge", "corn flakes", "honey ",
    )),
)

# Food phrases that contain drink/alcohol tokens but stay edible.
_EDIBLE_OVERRIDE = (
    "vinegar", "milk chocolate", "chocolate bar", "chocolate cake", "coffee cake",
    "tea biscuit", "wine gum", "wine vinegar", "coconut milk", "baking soda",
    "water cracker", "rose water", "beer batter",
)

# Whole-word keyword regexes, built once.
def _keyword_re(words: tuple[str, ...]) -> re.Pattern[str]:
    parts = []
    for raw in words:
        word = raw.strip()
        if not word:
            continue
        parts.append(re.escape(word).replace(r"\ ", r"[\s/-]+"))
    if not parts:
        return re.compile(r"(?!x)x")
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])", re.I)


_EDIBLE_OVERRIDE_RE = _keyword_re(_EDIBLE_OVERRIDE)
_RULE_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (slug, _keyword_re(words)) for slug, words in _RULES
)


def is_valid_macro(category: str | None) -> bool:
    return (category or "").strip() in _VALID


def normalize_macro(category: str | None) -> str:
    c = (category or "").strip()
    return c if c in _VALID else ""


def macro_label(category: str | None) -> str:
    c = normalize_macro(category)
    return MACRO_LABELS.get(c, "") if c else ""


def classify_macro(name: str, extra: str = "") -> str:
    """Guess macro-category from receipt / official product names."""
    blob = f"{name or ''} {extra or ''}".strip()
    if not blob:
        return OTHER
    if _EDIBLE_OVERRIDE_RE.search(blob):
        pass  # fall through — overrides block false drink matches below
    for slug, pattern in _RULE_RES:
        if not pattern.search(blob):
            continue
        if slug in (WATER, SOFT_DRINKS, JUICES, COFFEE_TEA, ALCOHOL) and _EDIBLE_OVERRIDE_RE.search(blob):
            continue
        return slug
    return OTHER


def classify_macro_from_open_facts(category: str) -> str:
    """Map Open Food Facts free-text category to a macro slug (best effort)."""
    blob = (category or "").lower()
    if not blob:
        return ""
    hints: tuple[tuple[str, tuple[str, ...]], ...] = (
        (CHEESE, ("cheese", "fromage")),
        (FISH_SEAFOOD, ("fish", "seafood", "tuna", "salmon")),
        (MEAT, ("meat", "beef", "pork", "lamb")),
        (POULTRY, ("poultry", "chicken")),
        (DAIRY, ("dairy", "yogurt", "milk")),
        (SWEETS_CHOCOLATE, ("chocolate", "confection", "candy", "biscuit")),
        (SOFT_DRINKS, ("soft drink", "soda", "cola")),
        (JUICES, ("juice",)),
        (WATER, ("water", "mineral")),
        (ALCOHOL, ("beer", "wine", "spirit")),
        (BAKERY, ("bread", "bakery")),
        (PASTA_RICE_GRAINS, ("pasta", "rice", "cereal grain")),
        (OILS_VINEGARS_CONDIMENTS, ("oil", "vinegar", "sauce")),
        (SAVORY_SNACKS, ("snack", "crisp", "chip")),
        (PERSONAL_CARE, ("shampoo", "cosmetic", "toothpaste")),
        (HOUSEHOLD_CLEANING, ("detergent", "cleaning")),
    )
    for slug, words in hints:
        if any(w in blob for w in words):
            return slug
    return ""
