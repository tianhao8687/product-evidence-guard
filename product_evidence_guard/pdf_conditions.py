"""Resolve printed footnote markers without guessing document-wide conditions.

Geometry finds raised references; exact markers find definitions. Repeated or
missing definitions remain explicit uncertainty, never an unconditional fact.
The existing candidate/provenance/scope contracts carry the result downstream.
"""
from collections import defaultdict
import re

from .pdf_layout import MAX_PAGE_CHARACTERS, _bbox, _inside, _lines, _locator, _text


def raised_reference_words(words):
    references = []
    for line in _lines(words):
        bodies = [w for w in line if re.search(r"[A-Za-z\u3400-\u9fff]", w["text"])]
        if not bodies:
            continue
        for word in line:
            if not re.fullmatch(r"\d{1,2}\)?|[*†‡]", word["text"]):
                continue
            prior = [w for w in bodies if -.5 <= word["x0"] - w["x1"] <= 4]
            if not prior:
                continue
            body = prior[-1]
            if word["text"] in {"2", "3"} and re.fullmatch(r"(?:\d+(?:\.\d+)?\s*)?(?:[cmµun]?m|s)", body["text"]):
                continue  # a squared/cubic unit is not a footnote
            if (word["bottom"] - word["top"] < .85 * (body["bottom"] - body["top"])
                    and word["top"] <= body["top"] + .5 and word["bottom"] < body["bottom"] - 1):
                references.append(word)
    return references


def _note_start(line, page_height):
    text = _text(line)
    match = re.match(r"^(\*?\d{1,2}|[*†‡])\s*[.=、)]\s+(.+)", text)
    if not match and line[0]["top"] > page_height * .4:
        match = re.match(r"^(\d{1,2}|[*†‡])\s+([A-Za-z\u3400-\u9fff].{14,})", text)
    return match


def collect_condition_notes(page, number):
    if len(page.chars) > MAX_PAGE_CHARACTERS:
        return []
    words = page.dedupe_chars().extract_words(x_tolerance=2, y_tolerance=3, extra_attrs=["size"])
    lines = _lines(words)
    notes = []
    for index, line in enumerate(lines):
        match = _note_start(line, page.height)
        if not match:
            continue
        value, used = match[2], list(line)
        last = line
        for following in lines[index + 1:]:
            gap = following[0]["top"] - last[0]["top"]
            height = max(w["bottom"] - w["top"] for w in line)
            text = _text(following)
            if (gap > height * 1.8 or gap <= 0 or abs(following[0]["x0"] - line[0]["x0"]) > 12
                    or _note_start(following, page.height)
                    or max(w["bottom"] - w["top"] for w in following) > height * 1.25):
                break
            value += " " + text
            used.extend(following)
            last = following
        if 5 <= len(value) <= 2000:
            notes.append({"marker": match[1].lstrip("*") or "*", "text": value, "locator": _locator(page, number, _bbox(used))})
    return notes


def attach_condition_notes(blocks, notes):
    by_marker = defaultdict(list)
    for note in notes:
        by_marker[note["marker"]].append(note)
    reference_pages = defaultdict(set)
    for block in blocks:
        for marker in block.provenance.get("condition_refs", []):
            reference_pages[marker].add(block.locator.get("page"))
    for block in blocks:
        refs = block.provenance.get("condition_refs", [])
        if not refs:
            continue
        resolved, unresolved = [], []
        for marker in refs:
            choices = by_marker[marker]
            local = [note for note in choices if note["locator"]["page"] == block.locator.get("page")]
            choices = local or (choices if len(reference_pages[marker]) == 1 else [])
            # Same page takes precedence. Never choose the nearest *different*
            # definition when several pages reuse numbering for separate tables.
            meanings = {note["text"] for note in choices}
            if len(meanings) == 1:
                resolved.append(choices[0])
            else:
                unresolved.append(marker)
        block.provenance["source_conditions"] = resolved
        block.provenance["unresolved_condition_refs"] = unresolved
