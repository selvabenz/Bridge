import { describe, expect, it } from "vitest";

import {
  LANGUAGE_QA_CATEGORY_MARKS, buildSegments, categoryClass, languageQaMarkClass, languageQaMarkRank,
} from "../../utils/highlight";
import type { AiCheckReview } from "../../types/finding";
import type { LanguageQaCategory, LanguageQaFinding } from "../../types/languageQa";
import { lqaFinding } from "./languageQaFixture";

function termFinding(overrides: Partial<LanguageQaFinding> = {}): LanguageQaFinding {
  return lqaFinding({
    id: "t1", verse: "9", rule: "terminology.deprecated-form", start: 6, end: 10,
    originalText: "beta", message: "Deprecated form.", suggestedReplacement: "gamma", ...overrides,
  });
}

function aiReview(overrides: Partial<AiCheckReview> = {}): AiCheckReview {
  return {
    tool: "translationNotes",
    group_id: "figs-metaphor",
    check_id: "tn-1",
    source_quote: "λόγος",
    proposed_selection_ids: [],
    proposed_selection_text: [],
    proposed_selections: [{ text: "beta", occurrence: 1, occurrences: 1 }],
    nothing_to_select: false,
    verdict: "problem",
    severity: "high",
    rationale: "The metaphor is not carried over.",
    suggested_correction: "",
    confidence: 0.9,
    evidence_used: [],
    selection_state: "proposed" as never,
    semantic_mapping: null,
    ...overrides,
  };
}

const TEXT = "alpha beta gamma";

function marked(segments: ReturnType<typeof buildSegments>): Array<[string, string | null]> {
  return segments.filter((s) => s.className).map((s) => [s.text, s.className]);
}

describe("verse-text underlines", () => {
  it("maps each finding source to its own colour class", () => {
    expect(categoryClass("translation_note")).toBe("m-tn");
    expect(categoryClass("translation_word")).toBe("m-tw");
    expect(categoryClass("alignment")).toBe("m-align");
    // Everything Greek Room produces shares one class.
    expect(categoryClass("unicode")).toBe("m-gr");
    expect(categoryClass("structure")).toBe("m-gr");
  });

  it("underlines a tN issue the AI flagged", () => {
    const segments = buildSegments(TEXT, [], [], [aiReview()]);
    expect(marked(segments)).toEqual([["beta", "m-tn"]]);
  });

  it("underlines a tW issue with the tW colour", () => {
    const segments = buildSegments(TEXT, [], [], [aiReview({ tool: "translationWords" })]);
    expect(marked(segments)).toEqual([["beta", "m-tw"]]);
  });

  it("underlines a review verdict, which still needs a human look", () => {
    const segments = buildSegments(TEXT, [], [], [aiReview({ verdict: "review" })]);
    expect(marked(segments)).toEqual([["beta", "m-tn"]]);
  });

  it("underlines a proposal the AI passed, whatever the verdict", () => {
    // The mark says "this is where the check landed", not "this is wrong".
    // In Manual reviewer mode nothing is auto-applied, so a passing proposal
    // is the only thing marking the words until a human applies it --
    // filtering these out blanked the verse until you selected text by hand.
    for (const verdict of ["pass", "problem", "review", "not_applicable"] as const) {
      const segments = buildSegments(TEXT, [], [], [aiReview({ verdict })]);
      expect(marked(segments), `verdict ${verdict}`).toEqual([["beta", "m-tn"]]);
    }
  });

  it("marks nothing when the AI proposed no span", () => {
    const segments = buildSegments(TEXT, [], [], [aiReview({
      verdict: "problem", nothing_to_select: true,
      proposed_selections: [], proposed_selection_text: [],
    })]);
    expect(marked(segments)).toEqual([]);
  });

  it("underlines a terminology.deprecated-form finding with its own class, not m-gr", () => {
    const segments = buildSegments(TEXT, [], [], [], [termFinding()]);
    expect(marked(segments)).toEqual([["beta", "m-term"]]);
  });

  it("marks a tamil.vallinam-missing finding with the sandhi class, not m-term", () => {
    const segments = buildSegments(TEXT, [], [], [], [termFinding({
      rule: "tamil.vallinam-missing", suggestedReplacement: "betak",
    })]);
    expect(marked(segments)).toEqual([["beta", "m-lqa-sandhi"]]);
  });

  it("draws only findings the engine marked inline, whatever their rule", () => {
    expect(marked(buildSegments(TEXT, [], [], [], [termFinding({ rule: "tamil.wordlist-variant" })]))).toEqual([]);
    // The engine's flag decides, not the rule name.
    expect(marked(buildSegments(TEXT, [], [], [], [termFinding({ inline: false })]))).toEqual([]);
    expect(marked(buildSegments(TEXT, [], [], [], [termFinding({
      rule: "spacing.extra", layer: "integrity", category: "spacing", inline: true,
    })]))).toEqual([["beta", "m-lqa-spacing"]]);
  });

  it("draws an indic-qa finding the engine says is drawn, though its rule is not reviewed inline", () => {
    // Settings > Language QA: `drawn` is the engine's answer under the user's
    // threshold; `inline` stays the reviewed flag the CI human gate reads.
    const consistency = { rule: "hi.style.ye-ending-consistency", category: "consistency" as const, inline: false };
    expect(marked(buildSegments(TEXT, [], [], [], [termFinding({ ...consistency, drawn: true })])))
      .toEqual([["beta", "m-lqa-typo"]]);
    expect(marked(buildSegments(TEXT, [], [], [], [termFinding({ ...consistency, drawn: false })]))).toEqual([]);
  });

  it("segments a dense verse with many drawn findings quickly", () => {
    // Every indic-qa finding is drawn by default now: a verse can carry many marks.
    const long = "word ".repeat(400);
    const findings = Array.from({ length: 200 }, (_, i) => termFinding({
      id: `f${i}`, start: (i * 7) % (long.length - 10), end: ((i * 7) % (long.length - 10)) + 6,
      originalText: long.slice((i * 7) % (long.length - 10), ((i * 7) % (long.length - 10)) + 6),
      inline: false, drawn: true,
    }));
    const started = performance.now();
    const segments = buildSegments(long, [], [], [], findings);
    expect(performance.now() - started).toBeLessThan(250);
    expect(segments.filter((s) => s.className).length).toBeGreaterThan(0);
  });
});

describe("Language QA indicator per category", () => {
  const cases: Array<[Partial<LanguageQaFinding>, string]> = [
    [{ category: "typo", confidence: "high", layer: "pattern" }, "m-lqa-typo-high"],
    [{ category: "typo", confidence: "medium", layer: "pattern" }, "m-lqa-typo"],
    [{ category: "typo", confidence: "low", layer: "integrity" }, "m-lqa-typo"],
    [{ category: "typo", confidence: "high", layer: "lexicon" }, "m-lqa-typo"],
    [{ category: "sandhi", confidence: "high", layer: "pattern" }, "m-lqa-sandhi"],
    [{ category: "word-joining", confidence: "medium", layer: "pattern" }, "m-lqa-sandhi"],
    [{ category: "punctuation", confidence: "medium", layer: "integrity" }, "m-lqa-spacing"],
    [{ category: "spacing", confidence: "low", layer: "integrity" }, "m-lqa-spacing"],
    [{ category: "unicode", confidence: "high", layer: "integrity" }, "m-lqa-unicode"],
    [{ category: "termbase", confidence: "high", layer: "housestyle" }, "m-term"],
    [{ category: "name", confidence: "medium", layer: "housestyle" }, "m-term"],
  ];
  it.each(cases)("%o -> %s", (overrides, expected) => {
    expect(languageQaMarkClass(lqaFinding(overrides))).toBe(expected);
    expect(marked(buildSegments(TEXT, [], [], [], [termFinding({ ...overrides, inline: true })])))
      .toEqual([["beta", expected]]);
  });

  it("styles every category the engine can send", () => {
    const categories: LanguageQaCategory[] = ["typo", "sandhi", "word-joining", "punctuation", "unicode", "spacing", "termbase", "name", "usfm",
      "consistency", "grammar"];
    expect(Object.keys(LANGUAGE_QA_CATEGORY_MARKS).sort()).toEqual([...categories].sort());
  });
});

describe("indic-qa profile pack categories", () => {
  it("draws a consistency lead like a possible typo and a grammar lead like a sandhi lead", () => {
    expect(languageQaMarkClass({ category: "consistency", confidence: "low", layer: "lexicon" })).toBe("m-lqa-typo");
    expect(languageQaMarkClass({ category: "grammar", confidence: "low", layer: "pattern" })).toBe("m-lqa-sandhi");
  });
});

describe("overlapping Language QA marks", () => {
  // "beta gamma" is flagged twice: [6,16) and [11,16) overlap on "gamma".
  const wide = (overrides: Partial<LanguageQaFinding>) =>
    termFinding({ id: "wide", start: 6, end: 16, originalText: "beta gamma", ...overrides });
  const narrow = (overrides: Partial<LanguageQaFinding>) =>
    termFinding({ id: "narrow", start: 11, end: 16, originalText: "gamma", ...overrides });

  it("lets the higher severity supply the one Language QA class, whichever comes first", () => {
    const sandhiMedium = { rule: "tamil.vallinam-missing", category: "sandhi" as const, layer: "pattern" as const, severity: "medium", confidence: "medium" as const };
    const termHigh = { category: "termbase" as const, layer: "housestyle" as const, severity: "high", confidence: "high" as const };
    for (const order of [[wide(sandhiMedium), narrow(termHigh)], [narrow(termHigh), wide(sandhiMedium)]]) {
      const segments = buildSegments(TEXT, [], [], [], order);
      expect(marked(segments)).toEqual([["beta ", "m-lqa-sandhi"], ["gamma", "m-term"]]);
      const overlap = segments.find((s) => s.text === "gamma")!;
      expect(overlap.findingIds).toEqual(["narrow", "wide"]);  // the primary finding first
    }
  });

  it("breaks a severity tie by category order: typo before sandhi before spacing before unicode before termbase", () => {
    const at = (category: LanguageQaCategory, id: string) =>
      termFinding({ id, category, layer: "pattern", severity: "medium", confidence: "medium", start: 6, end: 10 });
    const ranked = [at("unicode", "u"), at("termbase", "t"), at("spacing", "s"), at("sandhi", "d"), at("typo", "y")]
      .sort((a, b) => languageQaMarkRank(a) - languageQaMarkRank(b))
      .map((f) => f.id);
    expect(ranked).toEqual(["y", "d", "s", "u", "t"]);
    const segments = buildSegments(TEXT, [], [], [], [at("unicode", "u"), at("sandhi", "d")]);
    expect(marked(segments)).toEqual([["beta", "m-lqa-sandhi"]]);
  });

  it("keeps the other sources' classes alongside the one Language QA class", () => {
    const segments = buildSegments(TEXT, [], [], [aiReview()], [termFinding({})]);
    expect(marked(segments)).toEqual([["beta", "m-tn m-term"]]);
  });
});
