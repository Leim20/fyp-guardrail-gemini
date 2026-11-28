"""Text-level injection detection for the input guard (FR01): the rule scanner and the
fine-tuned classifier from P2, combined.

A text is flagged when either layer fires (rules score >= rule threshold, or classifier score >=
the threshold for the item's scan strictness). For retrieved content the detector also scores
each sentence, so SANITIZE can remove only the injected sentences and keep the useful rest of a
review or forum post. Sentence scoring also catches an injection diluted inside a long benign
document, which the whole-text score can average away.

Sentences are always scored at the normal threshold, never the strict one. The classifier was
trained on whole texts, so a lone short sentence is out of distribution for it; at the strict
threshold (~3e-4) that noise removed harmless lines such as "Don't panic!" (2026-11-13
diagnostic on forum_delivery_delay.md). The strict threshold applies to the whole document.
The "[title] (source)" line that search_kb puts on top of a document is metadata the tool
wrote, not third-party text, so it is not scored.

Every flag keeps its scores, so the confidence cascade (guardrail/guard.py) can tell a confident
flag (a rule fired, or the classifier is above the confident band) from an uncertain one that
goes to the judge.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from guardrail.rules import RuleScanner

MIN_SENTENCE_CHARS = 12  # shorter fragments (headings, "Thanks!") carry no instruction
MAX_SENTENCE_CHARS = 350  # inside the classifier's 512 tokens even for Chinese (about a token a character)
REMOVED = "[removed by the guard: instruction-like text]"
# Latin sentence ends need a following space (so "v1.2" and "RM3.50" stay whole); Chinese and Japanese
# full-width ends do not, because those scripts put no space between sentences.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])|\n+")
_HEADER = re.compile(r"^\[[^\]\n]*\] \([a-z_]+\)$")


def canonical(text: str) -> str:
    """The text as the detectors read it. Unicode offers many ways to write what a person reads
    as the same words: zero-width characters inside a word, full-width or ligature letters. A
    regular expression, and a classifier trained on plain text, see different strings. NFKC
    folds the compatibility forms, and invisible format characters (category Cf: zero-width
    space and joiners, soft hyphen, direction marks) are dropped. Only the detectors read this
    form; the text passed on to the agent is left as written. Look-alike letters from another
    script (a Cyrillic "o") are not folded: that needs a confusables table, a known limitation."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if unicodedata.category(ch) != "Cf")


def split_sentences(text: str) -> list[str]:
    """Sentences, with any run longer than MAX_SENTENCE_CHARS cut into pieces at spaces. The
    classifier reads at most 512 tokens (head and tail of a longer text), so without the cut
    the middle of a long unpunctuated passage would never be read by it."""
    pieces = []
    for part in (p.strip() for p in _SENTENCE_END.split(text)):
        while len(part) > MAX_SENTENCE_CHARS:
            cut = part.rfind(" ", 0, MAX_SENTENCE_CHARS)
            cut = cut if cut > 0 else MAX_SENTENCE_CHARS
            pieces.append(part[:cut].strip())
            part = part[cut:].strip()
        pieces.append(part)
    return [s for s in pieces if s]


@dataclass
class Signal:
    """One scored span: the whole text or a sentence."""
    rule: float | None
    ml: float | None
    by_rules: bool = False       # the rule score reached the rule threshold
    by_classifier: bool = False  # the classifier score reached this span's threshold

    @property
    def flagged(self) -> bool:
        return self.by_rules or self.by_classifier

    def confident(self, confident_ml: float) -> bool:
        """A rule fired (rules are precise), or the classifier is in its confident band."""
        return self.by_rules or (self.ml is not None and self.ml >= confident_ml)


@dataclass
class Detection:
    whole: Signal
    threshold: float | None = None
    rule_ids: list[str] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)
    signals: dict[str, Signal] = field(default_factory=dict)  # scored sentences only
    cleared: set[str] = field(default_factory=set)  # flags the judge overruled ("__whole__" or a sentence)
    judge: dict | None = None  # the escalation, if there was one

    @property
    def flagged_sentences(self) -> list[str]:
        return [s for s in self.sentences if s in self.signals and self.signals[s].flagged and s not in self.cleared]

    @property
    def flagged(self) -> bool:
        return (self.whole.flagged and "__whole__" not in self.cleared) or bool(self.flagged_sentences)

    @property
    def sanitized(self) -> str | None:
        bad = set(self.flagged_sentences)
        return " ".join(REMOVED if s in bad else s for s in self.sentences) if bad else None

    def layers(self) -> list[str]:
        """Which detection layers raised a flag that still stands: "rules", "classifier" or both."""
        spans = ([self.whole] if "__whole__" not in self.cleared else []) + [self.signals[s] for s in self.flagged_sentences]
        return [name for name, fired in (("rules", any(s.by_rules for s in spans)),
                                         ("classifier", any(s.by_classifier for s in spans))) if fired]

    def scores(self) -> dict[str, float]:
        return {k: round(float(v), 4) for k, v in (("rules", self.whole.rule), ("classifier", self.whole.ml))
                if v is not None}


class Detector:
    def __init__(self, rules: RuleScanner | None, classifier, thresholds: dict[str, float], rule_threshold: float):
        if rules is None and classifier is None:
            raise ValueError("a detector needs at least one layer")
        self.rules, self.classifier = rules, classifier
        self.thresholds, self.rule_threshold = thresholds, rule_threshold

    def _signals(self, texts: list[str], thresholds: list[float | None]) -> list[Signal]:
        rule_scores = [self.rules.score(t) for t in texts] if self.rules else [None] * len(texts)
        ml_scores = [float(s) for s in self.classifier.score(texts)] if self.classifier else [None] * len(texts)
        return [Signal(r, m, r is not None and r >= self.rule_threshold, m is not None and m >= t)
                for r, m, t in zip(rule_scores, ml_scores, thresholds)]

    def inspect(self, text: str, strictness: str, by_sentence: bool) -> Detection:
        sentences = split_sentences(text) if by_sentence else []
        scored = [s for s in sentences if len(s) >= MIN_SENTENCE_CHARS and not _HEADER.match(s)]
        thresholds = [self.thresholds[strictness]] + [self.thresholds["normal"]] * len(scored)
        signals = self._signals([canonical(t) for t in [text] + scored], thresholds)
        return Detection(whole=signals[0], threshold=self.thresholds[strictness],
                         rule_ids=sorted({m.rule_id for m in self.rules.scan(canonical(text))}) if self.rules else [],
                         sentences=sentences, signals=dict(zip(scored, signals[1:])))
