"""Industries, niches and units for business setup, matched locally as the owner types (no API call).

Each industry lists common niches and extra search words (Banglish and Bangla) so a custom niche can be filed under the
nearest category while the owner's own words are kept unchanged.
"""
import re

INDUSTRIES = {
    'jewelry': ('Jewelry and precious metals', ['Gold trading and jewelry retail', 'Silver jewelry', 'Diamond jewelry', 'Gold wholesale', 'Jewelry manufacturing'],
                'gold silver diamond jewellery jewelry sona rupa gohona bhori tola স্বর্ণ সোনা রূপা গহনা জুয়েলারি'),
    'apparel': ('Clothing and fashion', ['Men\'s clothing', 'Women\'s clothing', 'Kids\' clothing', 'Saree and three-piece', 'Garments export', 'Shoes and footwear', 'Fabric and textiles'],
                'cloth clothing fashion garments dress shirt saree panjabi borka shoes kapor kapor-chopor পোশাক কাপড় শাড়ি জুতা'),
    'grocery': ('Grocery and food retail', ['Grocery shop', 'Supermarket', 'Rice and grain trading', 'Edible oil and spices', 'Fruit and vegetables', 'Fish and meat'],
                'grocery mudi dokan rice chal dal oil tel moshla food bazar মুদি চাল ডাল তেল মসলা বাজার'),
    'restaurant': ('Restaurants and food service', ['Restaurant', 'Cafe and bakery', 'Fast food', 'Catering', 'Cloud kitchen', 'Sweets and confectionery'],
                   'restaurant hotel cafe bakery food khabar mishti catering রেস্টুরেন্ট খাবার মিষ্টি বেকারি'),
    'electronics': ('Electronics and mobile', ['Mobile phones and accessories', 'Computers and laptops', 'Home appliances', 'Electronics repair', 'Solar and electrical'],
                    'mobile phone laptop computer electronics electric solar tv fridge মোবাইল কম্পিউটার ইলেকট্রনিক্স'),
    'pharmacy': ('Pharmacy and health', ['Pharmacy', 'Medical equipment', 'Diagnostic centre', 'Clinic'],
                 'pharmacy medicine oushod dawai clinic doctor health ঔষধ ফার্মেসি ক্লিনিক'),
    'construction': ('Construction and building materials', ['Cement, rod and sand', 'Hardware and sanitary', 'Tiles and paint', 'Contractor services', 'Real estate'],
                     'construction rod cement sand brick hardware paint tiles building real estate রড সিমেন্ট বালু ইট'),
    'agriculture': ('Agriculture and livestock', ['Poultry farm', 'Dairy and cattle', 'Fish farming', 'Seeds and fertiliser', 'Crop farming'],
                    'farm agriculture poultry murgi dairy goru fish chas krishi fertilizer সার কৃষি খামার মুরগি গরু মাছ'),
    'cosmetics': ('Beauty and cosmetics', ['Cosmetics retail', 'Beauty parlour and salon', 'Skin care brand', 'Perfume'],
                  'cosmetics beauty parlour salon makeup skin care perfume কসমেটিক্স পার্লার'),
    'furniture': ('Furniture and home', ['Furniture shop', 'Furniture manufacturing', 'Home decor', 'Kitchenware'],
                  'furniture wood khat almari decor kitchen ফার্নিচার আসবাব'),
    'automotive': ('Automotive and transport', ['Car and bike parts', 'Vehicle repair', 'Transport and logistics', 'Courier', 'Ride and rental'],
                   'car bike motorcycle parts garage transport truck courier logistics গাড়ি মোটরসাইকেল পরিবহন'),
    'education': ('Education and training', ['Coaching centre', 'School', 'Online courses', 'Books and stationery'],
                  'education coaching school course tuition books stationery শিক্ষা কোচিং বই'),
    'it': ('Software and IT services', ['Software development', 'Web and digital agency', 'IT support', 'Freelancing'],
           'software it web app digital agency freelance marketing সফটওয়্যার আইটি'),
    'ecommerce': ('Online shop and e-commerce', ['Facebook page shop', 'Online store', 'Dropshipping', 'Marketplace seller'],
                  'online ecommerce f-commerce facebook page shop daraz store অনলাইন'),
    'manufacturing': ('Manufacturing and industry', ['Small factory', 'Packaging', 'Plastic products', 'Leather goods', 'Printing'],
                      'factory manufacturing production printing packaging plastic leather কারখানা উৎপাদন ছাপাখানা'),
    'services': ('Services', ['Tailoring', 'Cleaning', 'Event management', 'Travel agency', 'Consulting', 'Laundry'],
                 'service tailor dorji event travel agency consulting laundry সেবা দর্জি ট্রাভেল'),
    'wholesale': ('Wholesale and distribution', ['FMCG distribution', 'Import and trading', 'Dealer and agent'],
                  'wholesale distributor dealer import export trading paikari পাইকারি ডিলার আমদানি'),
    'other': ('Other', [], ''),
}
BUSINESS_TYPES = ('retail', 'wholesale', 'manufacturing', 'service', 'distribution', 'online')
CHANNELS = ('shop', 'facebook', 'website', 'marketplace', 'whatsapp', 'phone', 'wholesale', 'field_sales')
GOALS = ('profit', 'sales', 'costs', 'compete', 'new_markets', 'inventory', 'protect_capital', 'retention', 'grow_lean')
UNITS = {
    'bhori': ('bhori', 'vori', 'tola', 'ভরি', 'তোলা'), 'gram': ('gram', 'g', 'gm', 'grams', 'গ্রাম'), 'kg': ('kg', 'kilo', 'kilogram', 'কেজি'),
    'piece': ('piece', 'pcs', 'pc', 'pis', 'ta', 'টি', 'পিস'), 'litre': ('litre', 'liter', 'l', 'ltr', 'লিটার'), 'metre': ('metre', 'meter', 'm', 'গজ', 'মিটার'),
    'dozen': ('dozen', 'dz', 'ডজন'), 'box': ('box', 'carton', 'কার্টন'), 'bag': ('bag', 'sack', 'bosta', 'বস্তা'), 'set': ('set', 'সেট'),
    'hour': ('hour', 'hr', 'ghonta', 'ঘণ্টা'), 'service': ('service', 'job', 'session'), 'maund': ('maund', 'mon', 'মণ'),
}
# Products the market data layer can price later; the key travels with the product, the rest of the product stays sealed.
MARKET_KEYS = (
    ('gold_22k', r'\b22\s*k|22\s*carat|২২'), ('gold_21k', r'\b21\s*k|21\s*carat|২১'), ('gold_18k', r'\b18\s*k|18\s*carat|১৮'),
    ('gold_traditional', r'traditional|sanaton|সনাতন'), ('silver', r'silver|rupa|রূপা|রুপা'),
)

_WORD = re.compile(r'[\wঀ-৿]+', re.UNICODE)


def unit_of(text):
    """The standard unit for what the owner typed ('vori' → 'bhori'), or their own word when it is not a known unit."""
    clean = (text or '').strip().lower()
    for unit, words in UNITS.items():
        if clean == unit or clean in words:
            return unit
    return clean[:20]


def market_key(name, category, unit):
    text = f'{name} {category}'.lower()
    if not re.search(r'gold|sona|স্বর্ণ|সোনা|silver|rupa|রূপা|রুপা', text):
        return None
    for key, pattern in MARKET_KEYS:
        if re.search(pattern, text):
            return key
    return None


def classify(text):
    """Nearest industry for a free-text niche, or 'other'. The owner's own text is stored separately and never replaced."""
    words = set(_WORD.findall((text or '').lower()))
    best, score = 'other', 0
    for key, (label, niches, extra) in INDUSTRIES.items():
        vocabulary = set(_WORD.findall((label + ' ' + ' '.join(niches) + ' ' + extra).lower()))
        hits = sum(1 for w in words if w in vocabulary or (len(w) > 3 and any(v.startswith(w) or w.startswith(v) for v in vocabulary if len(v) > 3)))
        if hits > score:
            best, score = key, hits
    return best


def catalog():
    return dict(industries=[dict(key=k, label=v[0], niches=v[1], words=v[2]) for k, v in INDUSTRIES.items()],
                business_types=list(BUSINESS_TYPES), channels=list(CHANNELS), goals=list(GOALS), units=list(UNITS))
