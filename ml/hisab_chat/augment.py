"""Make one clean question read like the many ways real owners type it.

Owners misspell, shorten Banglish ("kto", "kmn", "tka"), drop punctuation, shout in capitals, add "vai" or "apu",
or write formally. Training on these variants teaches Hisab-Chat to understand the message, not one spelling of it.
"""
import random
import re

BANGLISH_SPELLINGS = {
    'koto': ('kto', 'kot', 'kotto'), 'ache': ('ase', 'ace', 'achhe'), 'kemon': ('kmn', 'kemne'), 'taka': ('tk', 'tka'),
    'hobe': ('hbe', 'hoibo'), 'hoyeche': ('hoise', 'hoilo', 'holo'), 'mashe': ('mase', 'mash e'), 'khoroch': ('khoros', 'khrch'),
    'dite': ('dibo', 'dewa lagbe'), 'paona': ('pawna', 'pauna'), 'bhai': ('vai', 'bro'), 'ekhon': ('akhon', 'ekn'),
    'amar': ('amr',), 'apnar': ('apnr',), 'customer': ('kastomar', 'custmr'), 'supplier': ('suplier', 'supplir'),
}
SUFFIXES = {'bl': (' vai', ' bhai', ' apu', ' boss', ' plz', ''), 'bn': (' ভাই', ' আপু', ''), 'en': (' please', ' pls', '')}
FORMAL = {
    'en': ('Could you please tell me {q}', 'Kindly let me know {q}', 'I would like to know {q}'),
    'bl': ('Apnar kache jante chai, {q}', 'Doya kore bolben {q}', '{q} — ektu janaben?'),
    'bn': ('অনুগ্রহ করে জানাবেন, {q}', 'আমি জানতে চাই, {q}', '{q} — একটু জানাবেন?'),
}


def typo(word, rng):
    if len(word) < 4 or not word.isascii():
        return word
    i = rng.randrange(1, len(word) - 1)
    kind = rng.random()
    if kind < 0.4:
        return word[:i] + word[i + 1:]                       # dropped letter
    if kind < 0.7:
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]  # swapped letters
    return word[:i] + word[i] + word[i:]                     # doubled letter


def noisy(text, lang, rng):
    words = text.split()
    out = []
    for word in words:
        bare = word.strip('?!.,').casefold()
        if lang == 'bl' and bare in BANGLISH_SPELLINGS and rng.random() < 0.5:
            word = rng.choice(BANGLISH_SPELLINGS[bare])
        elif rng.random() < 0.12:
            word = typo(word, rng)
        out.append(word)
    text = ' '.join(out)
    roll = rng.random()
    if roll < 0.25:
        text = re.sub(r'[?!.]', '', text)
    elif roll < 0.35:
        text = text.rstrip('?') + '??'
    if rng.random() < 0.1 and lang != 'bn':
        text = text.upper()
    elif rng.random() < 0.5:
        text = text.casefold() if lang != 'bn' else text
    return (text + rng.choice(SUFFIXES[lang])).strip()


def formal(text, lang, rng):
    question = text.rstrip('?').strip()
    return rng.choice(FORMAL[lang]).format(q=question[0].lower() + question[1:] if lang == 'en' else question)


def variants(text, lang, rng, count=4):
    """The original plus distinct noisy and formal variants."""
    seen, out = {text}, [(text, False)]
    for _ in range(count * 4):
        if len(out) > count:
            break
        is_formal = rng.random() < 0.25
        candidate = formal(text, lang, rng) if is_formal else noisy(text, lang, rng)
        if candidate not in seen:
            seen.add(candidate)
            out.append((candidate, is_formal))
    return out
