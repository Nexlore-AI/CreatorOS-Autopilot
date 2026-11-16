#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure the Hindi/English mix of a spec's narration, and lint the heavy-Hindi words.

    python3 shorts/langmix.py OUT/01_<slug>.json
    python3 shorts/langmix.py OUT/01_<slug>.json --target 45 55
    python3 shorts/langmix.py --text "लॉस एक पहाड़ है"

Why: the narration was written in near-pure Hindi (measured ~15-25% English across the
2026-11-15 and 11-16 Shorts), which reads as formal to the Gen-Z Indian dev audience this
channel is for. They say "loss", "step", "slow", "blow up" -- not "पहाड़", "कीमत",
"रेंगती". Target is 38-55% English *words*, with ~45% the sweet spot.

The band is empirical, not a guess. Rewriting two of the 2026-11-16 scenes as naturally as
possible landed at 44% and 39%, and that is about the ceiling: the Hindi skeleton (है,
हो जाती है, तो, रखो, चलते हैं, बताता है) is the grammar, and replacing it to chase a
higher number stops sounding like speech. So 38% is the floor worth enforcing and 55% is
where it starts reading as English with Hindi glue.

The script stays Devanagari. English words are written in Devanagari too (मॉडल, लॉस,
स्टेप), because Latin script makes ElevenLabs switch to an English accent mid-sentence --
measured and recorded in shorts/STYLE.md. So this raises how much English the listener
HEARS without changing the voice. Devanagari spelling is a TTS requirement, not a
language choice.

Exit code is 0 when the ratio is inside the target band and nothing is flagged, else 1,
so the daily run can gate on it like hookscore.py and title.py.

The ratio is a lower bound: it counts English only when the word is in the lexicon below.
An unrecognised English word reads as Hindi and pushes the number down, so add new
loanwords to ENGLISH as they come up rather than trusting a near-miss.
"""
import argparse
import json
import os
import re
import sys

# English loanwords as they are written in Devanagari in these specs. Extend freely.
ENGLISH = set("""
मॉडल मॉडल्स ट्रेनिंग ट्रेन लॉस ग्रेडिएंट ग्रेडिएंट्स स्टेप स्टेप्स लूप टूल टूल्स प्रॉम्प्ट प्रॉम्प्ट्स
नॉइज़ इमेज इमेजेज़ वेक्टर वेक्टर्स स्केल डिफ़ॉल्ट कैच एजेंट एजेंट्स चैटबॉट एआई पास पासेज़
फ़ॉरवर्ड रिवर्स यू-नेट स्टैटिक क्वालिटी लिमिट गोल लिस्ट आर्ग्युमेंट्स कोड रिज़ल्ट कोर वीडियो
फ़ॉलो थिंक एक्ट ऑब्ज़र्व मल्टिप्लाई साइज़ बैकप्रॉप एडम पाइटॉर्च डिफ़्यूज़न डिफ़्यूज़र्स गाइडेंस
प्रेडिक्शन प्रेडिक्शन्स प्रेडिक्ट गैप इग्नोर नंबर रेट लर्निंग फ़ाइन-ट्यूनिंग फ़ाइन-ट्यून आरएजी
वेट वेट्स लेयर लेयर्स डेटा डेटासेट जीपीयू सीपीयू कैश मेमोरी टोकन टोकन्स अटेंशन एम्बेडिंग
एम्बेडिंग्स ट्रांसफॉर्मर एन्कोडर डिकोडर सॉफ्टमैक्स बैच एपॉक ऑप्टिमाइज़र पैरामीटर पैरामीटर्स
बेंचमार्क एक्यूरेसी लेटेंसी थ्रूपुट कॉस्ट क्लाउड सर्वर एपीआई लाइब्रेरी फ़ंक्शन क्लास ऑब्जेक्ट
इनपुट आउटपुट फ़ीचर फ़ीचर्स लॉजिक बग फ़िक्स ट्रिक हैक सेटअप कॉन्फ़िग वर्ज़न रिलीज़ अपडेट
स्लो फ़ास्ट चीप एक्सपेंसिव स्मार्ट सिंपल ट्रिकी ओवरकिल बेसिकली एक्चुअली लिटरली डायरेक्ट
डायरेक्शन पार्ट पार्ट्स डिफ़रेंस वर्ड वर्ड्स रिड्यूस रिपीट गेम ब्लो अप ओवर-बेक्ड अंडर-बेक्ड
चेक चेंज कैलकुलेट रन स्किप स्टोर सेव लोड सर्च मैच कंपेयर स्विच पिक ड्रॉप ऐड रिमूव
कंप्यूटर सिस्टम नेटवर्क न्यूरल डीप मशीन लैंग्वेज मॉडलिंग जनरेट जनरेशन इंफ़रेंस प्रोसेस
राइट रॉन्ग बेटर बेस्ट वर्स्ट हाई लो बिग स्मॉल ज़ीरो वन टू थ्री फ़ोर फ़ाइव सिक्स सेवन एट नाइन टेन
कर्व अपहिल डाउनहिल ऑपोज़िट साइड सुपर प्रेटी क्वाइट वे टाइम स्पीड वैल्यू एरर मेथड एग्ज़ाम्पल
इन्फ़ो सबट्रैक्ट इम्प्रूव ऑलरेडी एक्स्ट्रा एक्स्ट्रीम नॉर्मल स्टेबल अनस्टेबल सेफ़ रिस्की
डिसाइड मूव ब्रेक स्टार्ट एडजस्ट माइनस प्लस ई ट्राई सेट गेट पुट कीप मेक टेक लेट
ओवरशूट अंडरशूट कन्वर्ज जंप क्रैश फ़ेल पास फ़िट ओवरफ़िट अंडरफ़िट ट्यून रीट्रेन
एक्स पॉइंट परसेंट सेकंड मिनट रियल फ़ेक टेस्ट प्रोडक्शन यूज़र यूज़ केस ओपन सोर्स क्लोज़्ड
""".split())

# Formal or literary Hindi this audience does not use when talking about code.
# word -> what to say instead (English, in Devanagari).
PREFER_ENGLISH = {
    "अनुसरण": "फ़ॉलो", "परिवर्तन": "चेंज", "गणना": "कैलकुलेट", "प्रशिक्षण": "ट्रेनिंग",
    "स्मृति": "मेमोरी", "संगणक": "कंप्यूटर", "कृत्रिम": "एआई (कृत्रिम बुद्धि कहने की ज़रूरत नहीं)",
    "पहाड़": "हिल / कर्व", "कीमत": "कॉस्ट", "दिशा": "डायरेक्शन", "हिस्सा": "पार्ट",
    "हिस्सों": "पार्ट्स", "फ़र्क़": "डिफ़रेंस", "फ़र्क": "डिफ़रेंस", "शब्द": "वर्ड्स",
    "घटाओ": "रिड्यूस करो", "दोहराओ": "रिपीट करो", "खेल": "गेम", "रेंगती": "स्लो हो जाती",
    "उछलकर": "ब्लो अप होकर", "गुणा": "मल्टिप्लाई", "जोड़": "ऐड", "घटाव": "सबट्रैक्ट",
    "संख्या": "नंबर", "आकार": "साइज़", "परत": "लेयर", "भार": "वेट", "त्रुटि": "एरर",
    "उपकरण": "टूल", "लक्ष्य": "गोल", "सूची": "लिस्ट", "परिणाम": "रिज़ल्ट",
    "गति": "स्पीड", "मूल्य": "वैल्यू", "चरण": "स्टेप", "विधि": "मेथड",
    "उदाहरण": "एग्ज़ाम्पल", "प्रक्रिया": "प्रोसेस", "जानकारी": "डेटा / इन्फ़ो",
    "अनुमान": "प्रेडिक्शन", "सुधार": "फ़िक्स / इम्प्रूव", "गुणवत्ता": "क्वालिटी",
}

WORD = re.compile(r"[ऀ-ॿ]+(?:-[ऀ-ॿ]+)*|[A-Za-z]+")


def narration(spec_path):
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    lines = []
    for sc in spec.get("scenes") or []:
        v = (sc.get("voice") or "").strip()
        if v:
            lines.append(v)
    return lines


def analyse(lines):
    words, eng, latin, flagged = [], [], [], []
    for ln in lines:
        for w in WORD.findall(ln):
            words.append(w)
            if re.fullmatch(r"[A-Za-z]+", w):
                latin.append(w)
                eng.append(w)
            elif w in ENGLISH:
                eng.append(w)
            else:
                # match the bare stem too, so "हिस्सों" and "हिस्सा" both flag
                for bad, good in PREFER_ENGLISH.items():
                    if w == bad or w.startswith(bad):
                        flagged.append((w, good))
                        break
    return words, eng, latin, flagged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?")
    ap.add_argument("--text", help="check a raw string instead of a spec")
    ap.add_argument("--target", nargs=2, type=int, default=[38, 55],
                    metavar=("LO", "HI"), help="target English%% band (default 38 55)")
    a = ap.parse_args()

    if a.text:
        lines = [a.text]
        label = "(--text)"
    elif a.spec:
        if not os.path.isfile(a.spec):
            sys.exit("no such spec: " + a.spec)
        lines = narration(a.spec)
        label = os.path.basename(a.spec)
    else:
        sys.exit("pass a spec path or --text")

    if not lines:
        sys.exit("no narration found: every scene's `voice` is empty")

    words, eng, latin, flagged = analyse(lines)
    lo, hi = a.target
    pct = 100.0 * len(eng) / len(words) if words else 0.0

    print(f"{label}: {len(words)} words, {len(eng)} English ({pct:.0f}%), target {lo}-{hi}%")
    print()
    for i, ln in enumerate(lines, 1):
        w, e, _, _ = analyse([ln])
        p = 100.0 * len(e) / len(w) if w else 0.0
        mark = " " if lo <= p <= hi else "!"
        print(f" {mark} scene {i}: {p:3.0f}%  {ln[:68]}")

    bad = False
    if latin:
        print()
        print(f"!! {len(latin)} word(s) in Latin script: {', '.join(sorted(set(latin))[:10])}")
        print("   Write them in Devanagari. Latin script makes the voice switch to an")
        print("   English accent mid-sentence (shorts/STYLE.md).")
        bad = True

    if flagged:
        print()
        print(f"!! {len(flagged)} formal-Hindi word(s) this audience would say in English:")
        for w, good in dict(flagged).items():
            print(f"   {w}  ->  {good}")
        bad = True

    print()
    if pct < lo:
        print(f"TOO HINDI: {pct:.0f}% English, want {lo}-{hi}%. Swap concept nouns, verbs and")
        print("adjectives to English (in Devanagari); keep Hindi for grammar and connectives.")
        bad = True
    elif pct > hi:
        print(f"TOO ENGLISH: {pct:.0f}%. Keep the Hindi skeleton -- है/होता है/करता है, तो,")
        print("क्योंकि, यही, समझो -- or it stops sounding like speech.")
        bad = True
    elif not bad:
        print(f"OK: {pct:.0f}% English, inside {lo}-{hi}%.")
    else:
        print(f"Ratio is fine at {pct:.0f}%, but fix the flags above.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
