"""Shared feature contract for the browser decoder and CRFsuite trainer."""
import json
import re
import unicodedata
from pathlib import Path

import regex

ROOT = Path(__file__).resolve().parents[2]
PATTERN = regex.compile(r"[\p{L}\p{M}\p{N}]+|[^\s]")
LEXICON = json.loads((ROOT/"web/src/taggerLexicon.json").read_text())
BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def tokenize(text):
    return PATTERN.findall(unicodedata.normalize("NFC", text))


def token_key(token):
    return token.translate(BN_DIGITS).lower()


DICTIONARIES = {group:{token_key(unicodedata.normalize("NFC", w)) for w in words}
                for group,words in LEXICON.items()}


def script(token):
    if re.fullmatch(r"[0-9]+",token):
        return "digit"
    if regex.fullmatch(r"[\p{Script=Bengali}]+",token):
        return "bn"
    if re.fullmatch(r"[a-z]+",token):
        return "latin"
    return "mixed"


def features(tokens):
    keys = [token_key(token) for token in tokens]
    rows = []
    for index, key in enumerate(keys):
        shape = re.sub(r"[a-z]", "a", re.sub(r"[0-9]", "d", key))
        shape = regex.sub(r"[\p{Script=Bengali}]", "b", shape)
        row = ["bias","w="+key,"script="+script(key),"shape="+shape]
        for length in range(1,min(4,len(key))+1):
            row.extend((f"p{length}="+key[:length],f"s{length}="+key[-length:]))
        for offset in (-2,-1,1,2):
            pos = index+offset
            neighbor = "<BOS>" if pos<0 else "<EOS>" if pos>=len(keys) else keys[pos]
            row.append(f"n{offset}="+neighbor)
        if key.isascii() and key.isdigit():
            row.append("is_number")
        for group, words in DICTIONARIES.items():
            if key in words:
                row.append("dict="+group)
        rows.append(row)
    return rows
