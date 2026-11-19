# PolyComic - what still needs work

State after milestone 3 (backend): Google sign-in; `POST /api/books` (login required, one
active book per user) starts a background job that makes 1 hero image + pages `BOOK_PAGES`
(default 1-3, Jack or Red), saves every picture to PostgreSQL as it finishes, streams
progress over SSE (`/api/books/{id}/events`) and builds a PDF. Books, pages, thumbnails,
the normalised photo and every provider call (`api_calls`) are stored per user. The old
`/api/hw2/book` route and the in-memory `BookStore` are gone. Everything below is known
to be missing or weak.

M4b (generation pipeline) adds: full books by default (`BOOK_PAGES=all`, 20 pages + cover +
About Me), family match from the photo, a character bible and scene plates in the database
(`scripts/pregenerate_assets.py`), a vision-graded quality loop (max 3 generations per
picture, best-of-3 fallback, attempts in events and in `page_attempts`), face-aware text
placement, a process-wide image-call limit, per-stage timing logs and `scripts/time_book.py`.
Everything in M4b was exercised only with `FakeProvider` + `FakeGrader`; none of it has run
against Gemini yet (no key used in this milestone).

## Quality tiers
- [x] Two tiers chosen by the buyer per book and per Quick Scenes run (`tier` form field,
      `app/tiers.py`): **Professional** (`PROFESSIONAL_IMAGE_MODEL`, empty = old
      `GEMINI_IMAGE_MODEL` / `PAGE_IMAGE_MODEL` behaviour; `PRICE_PROFESSIONAL_CENTS` 399)
      and **Cost-Efficient** (default; `COST_EFFICIENT_IMAGE_MODEL` =
      `gemini-3.1-flash-lite-image` for hero, family sheet and pages; `PRICE_COST_EFFICIENT_CENTS`
      99). `BOOK_PRICE_CENTS`, when set, overrides both prices (`=0` for free local tests).
      Same grader and quality loop in both. `books.tier` (migration `0006`, old rows =
      professional); per-tier averages in `/api/wallet` and `/admin`.
- [x] Cost-Efficient default chosen after a real test of `gemini-3.1-flash-lite-image`:
      good likeness and prop continuity at about $0.23 for the hero plus 3 pages.
      Still unknown: how often it needs quality-loop retries across a full 20-page book
      (each retry also costs a grade call), and the cost of a full book.
- [ ] `gemini-nano-banana-2.1` was NOT A/B tested against flash-lite on real outputs. It
      stays selectable via `COST_EFFICIENT_IMAGE_MODEL` (and is still priced in
      `app/providers/pricing.py`), but no likeness or prop comparison exists for it.
- [ ] Pre-generated bible sheets and plates are keyed by story + style only, NOT by tier.
      Assets made with the Pro model (`PREGEN_IMAGE_MODEL` defaults to
      `GEMINI_IMAGE_MODEL`) are used as references by both tiers on purpose - they are only
      reference images. If a cheap model copies Pro-rendered plates poorly, revisit.
- [ ] Margins are unknown. Output images alone for a clean 20-page book (hero + family
      sheet + 20 pages = 22 images) are about $2.95 on Pro (22 x $0.134) against $3.99, and
      about $0.74 on `gemini-nano-banana-2.1` (22 x $0.0336) against $0.99 - before input /
      reference-image tokens, grading and retries, so the Cost-Efficient margin may be thin
      or negative. Estimates from `pricing.py`; check the per-tier tiles in `/admin` once
      real books exist.
- [ ] No per-tier refund/upgrade path (e.g. redo a Cost-Efficient book as Professional).
- [ ] With `IMAGE_PROVIDER=openai` both tiers use `OPENAI_IMAGE_MODEL` (price differs only).
- [x] Per-book cost cap per tier (`COST_CAP_COST_EFFICIENT_USD` 0.97,
      `COST_CAP_PROFESSIONAL_USD` 3.70): optional calls (retries, the definite-error extra,
      family sheet regeneration, portraits, optional rate-limit re-tries) are skipped when
      they would pass it; definite-error retries first, soft retries leave 3 %, portraits
      6 %. Shared first-time cast sheets are not counted. Stored: `page_attempts.cost_stopped`,
      `books.cost_skips` (migration 0011, not yet applied).
- [ ] The cap is TIGHT for Cost-Efficient: by estimate the required calls of a clean Jack
      book (hero + 20 pages + 21 grades + family sheet) are already ~$0.92 (20 x $0.0336 of
      output images alone), so a capped book gets at most ~1 retry and Jack's 3 moment
      portraits are mostly skipped. Check real `api_calls` costs, then decide: raise the
      price, accept fewer retries, or shorten the grade prompt (~3.4k tokens per grade).
- [ ] Grader images now go at media_resolution medium (picture) / low (references)
      (`GRADER_MEDIA_RESOLUTION*`), ~$0.05 per book cheaper; unverified whether low-res
      references hurt the hair-texture / likeness checks on real outputs.

## Image quality / prompts
- [x] Third real run (Jack ps1, Red n64): grader `style_match` (vs hero reference + key
      technique words, repeated at the end of every page / hero / portrait prompt); Jack p18
      beanstalk lies fallen, straight and flat; per-page giant `sizes`; supporting sheets
      ranked by `ref_priority` (3 per page, giant first on p14); per-page `expressions` for
      supporting / family characters (graded, definite); no mirrored gestures (Red p8);
      canonical quilt, Red + Jack cottages and the castle as props (text only, no location
      sheets); hero sheet ignores patch / satchel-side trivia; close-up portrait check
      (`closeup_ok`); Red's whistle is a page extra from p2 (`hero_outfit.extras`); grader
      asks again once on invalid JSON. None of it verified on real outputs yet.
- [ ] Not yet pre-drawn: supporting sheets for all presets
      (`scripts/pregenerate_assets.py --story all --preset all --only sheets`).
- [ ] No prompt iteration with a real key yet. Every book test so far ran on `FakeProvider`
      (coloured placeholder rectangles). Hero likeness, outfit continuity and quiet text
      zones are unverified on `gemini-3-pro-image`.
- [ ] Default model is now `gemini-3-pro-image` (about $0.134 per 1K image, so roughly
      $0.54 for hero + 3 pages and about $2.80 for a full 20-page book before any retries).
      Cost vs. `gemini-nano-banana-2.1` has not been compared on real outputs.
- [ ] Output is 1K (around 900 x 1200 px). On a 216 x 286 mm page that is about 105 dpi,
      well below print quality (300 dpi needs about 2.5K x 3.4K). Need the 2K/4K tier or an
      upscaler before anyone prints a book.
- [x] ~~No quality loop~~ - `app/providers/gemini_vision.py` grades every picture (JSON
      mode, `response_schema`): likeness, fidelity, text artifacts, issues, face/key-object
      boxes. Fail -> regenerate with the issues as corrections, max 3 generations, then
      best score. A failed generation also uses an attempt (retried unless 400/401/403/404);
      a page with no picture after 3 tries fails the book (charge re-credited).
- [x] ~~Likeness gate trusts a single number~~ - the grader now also answers explicit
      identity checks against the ORIGINAL PHOTO (`skin_tone_match`, `hair_colour_match`,
      `hair_texture_match`, `identity_notes`). Any mismatch fails the picture whatever its
      likeness, with a correction built from the measured photo traits ("the hero's hair
      must be straight and black like in the photo"); the same rules apply to pages. The
      instructions say stylisation, a teen/adult photo, age and lighting are not mismatches.
      Every hero call (the first included) carries an identity line from the traits. Likeness thresholds stay at 0.6 (pages
      `QUALITY_MIN_LIKENESS`, hero `QUALITY_MIN_LIKENESS_HERO`): the first real "wrong hero"
      turned out to be a correct likeness of a different photo, scored 0.65.
- [ ] Identity checks are untested on real outputs: how often `gemini-3.8-flash` flags a
      correct stylised hero as a mismatch (each false alarm costs a regeneration) is unknown.
      Watch `page_attempts.issues` for "must be ... like in the photo".
- [x] ~~Hero lost curly hair texture~~ - a real Jack run (n64, photo: short, tight curly dark
      hair, traits said curly) drew straight swept hair and passed at likeness 0.72: the
      first hero call had no traits and the grader missed the texture. The first hero call
      now waits for the photo traits (about 2 s; the family match finished ~2 s after the
      hero started) and says "The child has <skin> skin and <texture> <colour> hair - keep
      exactly that." (curly/coily adds "must clearly read as curly: a bumpy, clumped
      silhouette"). A failed traits call starts the hero without the line. The family sheet
      still overlaps the hero. The grader's hair-texture instruction now compares the type
      category, looks closely at short curls and says a solid low-poly hair shape must
      still read as curly. Unverified on real outputs.
- [x] ~~Beans changed between pages~~ - page 2 showed five multicoloured beans, page 3 about
      ten purple ones (each page described them in its own words; the grader sees one
      page). Story-level `props` are now the single source of truth: pages list them
      (`props: [beans]`, unknown ids fail loading), the image prompt gets a "Props (draw
      exactly as described, same on every page)" block and the grader the same lines.
      Jack: `beans` (exactly five, one each red/yellow/green/blue/purple) on pages 2-3,
      `last_bean` (the single purple one) on 19, plus beanstalk / castle / golden egg.
      Red: basket, whistle, flowers, nightcap, wardrobe, cottage where they recur. Captions,
      plate prompts and must-be-visible were aligned (page 3: four beans fly, the purple
      one lands by the hero's boot). Text and titles unchanged. Pre-generated plates (none
      yet) would pick the block up too.
- [x] ~~Definite errors passed as "issues"~~ - the grader wrote "More than five beans are
      shown falling" and the page passed (0.72 / 0.75). The grader now returns
      `contradictions` (wrong count/colour, missing must-be-visible item, wrong character,
      prop mismatch) separately from softer `issues`; any contradiction fails the picture,
      leads the next corrections and costs 0.15 each (max 0.45) in best-of-3. How often
      `gemini-3.8-flash` raises false contradictions (each costs a regeneration, ~26 s) is
      unknown - watch `page_attempts.issues`.
- [x] ~~n64 outfit drawn with stitched patches~~ - presets have an optional `outfit_note`
      (n64: solid shapes, patches as flat painted colour shapes, no stitching/weave; ps1 and
      sprite16 equivalents) appended after the outfit line in hero, page, family sheet,
      bible and plate prompts. `hero_outfit` in the YAML is unchanged. Unverified on real
      outputs.
- [x] ~~Mother looks different from page to page~~ - one per-book **family sheet** (every
      family member of the story in the chosen variant, side by side, plain background, no
      hero) is generated right after the family match, beside the hero, and passed to every
      page with a family member ("Image N is the family reference sheet - Mother must look
      exactly like ..."). Skipped when bible sheets exist for the variant. One call, no
      quality loop, failure tolerated; `api_calls.kind = family_sheet`;
      `books.family_sheet_png` (migration `0005`). HW1 uses the same code.
- [ ] Family sheet quality is unverified on real outputs (whether the model draws exactly
      the listed people side by side, and whether pages copy them faithfully). If it hangs,
      family pages wait for it (up to the provider's 180 s timeout); there is no separate
      deadline.
- [x] ~~Hero hair drift / elderly mother~~ - a real 20-page Jack run (fam_c): page 16 drew
      the short-curled hero with long, loose, wind-blown hair ("hair blown upward" in the
      hero instruction) and passed at likeness 0.72 (the identity checks only compare hair
      TYPE with the photo); the family sheet drew the ~30-year-old mother as an elderly
      grey-haired woman, pages 1/17/18/20 copied it, pages 3/19 followed the text. Fixed:
      (a) p16 shows speed with the satchel strap and leaves instead; every page prompt now
      says the hero's hairstyle, length, colour and face stay as in the hero reference
      (wind never lengthens or restyles hair), family pages add that family members match
      their sheet (age, hair, face, outfit). (b) Family characters need an apparent `age`
      in the YAML (loader validates; Jack/Red mother 30, Grandma 70); descriptions say it,
      Jack's mother is "young ... kind, gentle face", worn clothes not an old person. The
      sheet prompt lists age + variant hair per figure and forbids grey hair / wrinkles for
      anyone under 60 (from the data). (c) One vision check of the sheet
      (`api_calls.kind = family_sheet_check`: count, no child, age, hair); a fail
      regenerates it ONCE with the notes (skipped once `QUALITY_TIME_BUDGET_S` is used up),
      a second fail keeps the sheet with fewer failed checks. Also in HW1 (its only retry).
      (d) The page grader gets the family reference (sheet, or the member's bible sheet) on
      family pages and returns `hero_hair_match` (length/shape/style/colour vs. the HERO
      REFERENCE) and `family_match` (age/hair vs. the sheet); a false value fails the page
      like the other identity checks, with a concrete correction. No DB change (the
      corrections are in `page_attempts.issues`).
- [ ] Remaining risk: all of the above ran only with `FakeProvider` + `FakeGrader`. Unknown:
      false-alarm rate of `hero_hair_match` on hair moved by action poses (each costs a
      page regeneration + grade), whether the vision model reliably judges apparent age on
      a stylised sheet, and the extra latency on real runs (sheet check ~2-5 s, normally
      hidden behind the hero; a sheet regeneration adds ~15-25 s to family pages only).
      Grade calls on family pages now send 4 images (768 px) instead of 3. Pre-generated
      bible sheets (if any) are not checked and were made without the age line - regenerate
      them. If the sheet itself is wrong after both tries, pages are graded against it.
- [ ] Grader thresholds (0.6 / 0.6) and the scoring weights are guesses; calibrate on real
      outputs. Unknown whether `gemini-3.8-flash` scores likeness consistently, or whether
      the configured thinking level is accepted (`GRADER_THINKING_LEVEL`, default
      `minimal`; a 400 naming "thinking" falls back to `low` once - any other 400 still
      means every picture is accepted unchecked; check the logs on the first real run).
- [ ] If the grader call fails the picture is accepted unchecked (`scores: null`) and its
      text goes to the layout's first zone with no box check (it may cover a face or a key
      object). A grader outage silently disables the quality loop and face-aware placement
      (failed `grade` rows in `api_calls` show it).
- [ ] No per-book cost cap. Worst case is ~3x the calls of a clean book (63 image + 63
      grade calls for hero + 20 pages instead of 21 + 21), roughly $8.50+ on the Pro model
      against a $3.99 Professional price.
- [x] ~~Background boxed as a key object~~ - page 3 was regenerated because "the garden
      below the window" was boxed and the text covered it. Placement now ignores key-object
      boxes over `OBJECT_MAX_AREA` (0.30) of the image or wider than 85% (regions, not
      objects; faces are never ignored), and the grader is told to box only discrete items.
- [x] ~~Planting bean as big as the child's head~~ - Jack's art fields (pages 2, 3, 19:
      caption, plate prompt, hero instruction, must-be-visible) now say "small ... about the
      size of a fingertip"; Red's whistle is "about the length of a finger". Text/titles
      unchanged. (`props.beans` is now the canonical five-bean prop, see below.)
- [x] ~~N64 style reads as a modern animated film~~ - the n64 technique and detail levels were
      rewritten (low poly everywhere incl. characters, mitten hands, flat per-vertex shading,
      no photoreal lighting / subsurface / AO, small blurry textures, no fabric detail, fog,
      skybox); it has its own likeness block ("simple bright edge highlight" instead of
      "soft rim light"; since replaced, see the next item). Unverified on real outputs;
      pre-generated n64 bible/plates (none yet) would need regenerating.
- [x] ~~Characters pasted in like stickers~~ - a real cost-efficient run
      (`gemini-3.1-flash-lite-image`, n64) drew the hero and Mother with thick white
      outlines on page 1 and a glowing edge on page 3; the grader only noted it as an
      issue, so the page passed. Fixes: (1) no preset asks for "rim light" / "edge
      highlight" any more - the n64 likeness block and the shared `LIKENESS_TRAITS`
      (ps1, sprite16) now say "lit by the scene's own light like everything else"; the
      n64 technique's "no subsurface skin glow" became "skin scattering"; sprite16 keeps
      its dark outlines but says "always dark, never white or light-coloured". (2) Every
      page prompt has `INTEGRATION_RULE` right after the reference list (re-draw every
      character in the scene's lighting with matching shadows, no outline / border / halo /
      glow / sticker edge), and the hero and family-sheet ref lines say they are for the
      look only, "not a cut-out to paste in". (3) The grader lists a cut-out/sticker look or
      a visibly pasted-in figure under contradictions (dark pixel-art outlines excluded),
      so it hard-fails and leads the next attempt's corrections.
      **Trade-off:** the homework's style traits are "large expressive eyes, rounded
      readable face, soft rim light". The first two are kept in every preset; the rim
      light is dropped as an explicit trait because cheaper models render any "light that
      separates the figure from the background" as a white outline or halo. A real rim
      light now only appears when the scene's own lighting makes one (e.g. Red's dusk
      page and Jack's firelit page ask for one in their `palette_note` / hero instruction
      - those were left as they are; watch them for halos). Figures may separate a little
      less from busy backgrounds. The hero and family-sheet refs are still drawn on plain
      light backgrounds; if pasting persists, try a mid-tone or simple in-world background
      for them. Unverified on real outputs; the grader's precision on this check (false
      fails on stylised edge shading) is unknown.
- [x] ~~Quota errors shown as "busy"~~ - a 429 with quota limit 0 / free tier / "exceeded your
      current quota" (without a per-minute metric) shows "Image generation isn't available
      right now.", logs an ERROR (billing not enabled on the key?) and is not retried. The
      message patterns are from Google's documented 429 texts and not checked against
      every variant.
- [ ] Gemini box coordinates: requested as `box_2d` [ymin, xmin, ymax, xmax] on 0-1000
      (Gemini's native format) and converted; not verified on real responses.
- [ ] Reference-image cap (14 for Gemini 3 image models, 3 for 2.5 Flash Image) is from
      Google's docs, not checked against the API; `IMAGE_MAX_REFS` overrides.
- [x] ~~`asyncio.gather` does not cancel the other page calls when one fails~~ - the job (and
      `run_book`, via `TaskGroup`) cancels the remaining page calls on the first failure and
      records them in `api_calls` as `cancelled`.
- [ ] Failed / cancelled calls are recorded with `est_cost_usd = 0`. Whether the provider
      bills a refused (safety) or cancelled-in-flight call is not known; a cancelled HTTP
      request may still be processed and billed upstream.
- [x] ~~`plate_prompt` is not used~~ - plates are pre-generated per story x preset from
      `plate_prompt` (hero and family members left out, other characters drawn from their
      bible sheets) and pages insert the hero + family into them. Falls back to the M3
      one-pass prompt when a plate is missing.
- [ ] Plates/bible are NOT generated yet and need a real key: ~180 assets (both stories x
      3 presets), about $24 at the Pro price. Run `scripts/pregenerate_assets.py --story all --preset all`
      against the production DB. Until then every page uses the fallback.
- [ ] Plates are made at the default detail level (2); books at detail 1 or 3 reuse them.
- [x] ~~Plate prompts in the YAML describe family members~~ - every `plate_prompt` now
      describes empty spots ("an empty space ... where a small person would stand") and
      never names the hero or a family member (tested). Only `plate_prompt` changed; text,
      captions, titles and hero instructions are as approved. The YAML header comment still
      says art fields call the hero "the hero child" (true for caption/hero_instruction).
- [ ] Whether the image model reliably leaves clean empty gaps in plates, and whether pages
      then place the hero/family convincingly into them, is untested on real outputs.
- [ ] Character drift between pages is still untested on real outputs (bible refs should
      help for Mother, the giant, the wolf).

### Full Cost-Efficient book: 81 s, $1.19, 10 retries (measured) - fixes, unverified
Measured on a real 20-page Jack book (`gemini-3.1-flash-lite-image`): 81 s (target < 60 s),
API cost $1.19 against the $0.99 price, 10 retries on 8 pages, page 10 failed all 3
attempts. Image calls ~9.6 s, grade calls ~10.0 s; without retries ~40 s. Most failures
were grader nitpicks, not errors. Everything below is tested with fakes only.
- [x] Contradictions calibrated: only objective, checkable errors (wrong count, wrong
      specified colour, missing must-be-visible item / required character, wrong or extra
      character, sticker look, blank band or border, inset/split panels or several images,
      text). Framing/cropping, how much is visible, pose/grip, relative size (unless the
      description gives a size and it is off > ~2x), motion state (unless the page's main
      event), lighting and background details are listed as NOT contradictions, with the
      real failures as examples (p6 beanstalk tip, p10 "fully visible giant", p12 egg
      size, p17 axe grip, p19 bean size). Watch `page_attempts.issues` on the next run to
      see whether the grader follows it; p18's "beanstalk not toppling" is still a
      contradiction by these rules (toppling IS that page's main event).
- [x] Jack p3: one bean phrasing everywhere ("exactly four small beans (red, yellow,
      green, blue) in the air between the window and the garden, and exactly one small
      purple bean on the doorstep: five beans in total, no others"); `props.beans` adds
      "five beans in total, never more". Text/title unchanged.
- [x] Slot 10 (shared full-face close-up: Jack p10 "peeking through the crack", Red p10
      "what big eyes") now prefers the TOP text zone (then bottom), and both pages keep the
      face in the middle band (chin well above the bottom fifth, about a third of the
      picture's height). Red p10's wolf eyes moved from the upper-right to the right side
      below the top fifth so they do not block the new first zone. Pre-generated slot-10
      plates (if any) were made with the old wording and should be regenerated.
- [x] Middle zone: the grader now boxes every character's body (`kind: "figure"`); a
      middle-band text zone touching any figure is blocked (top/bottom ignore figures).
      When every zone is blocked and the middle is the least bad, the corrective prompt
      names the layout's first zone. Unverified: whether the grader returns sensible
      figure boxes (and whether the extra boxes slow the grade call).
- [x] Blank-band check (`app/pipeline/bands.py`, local, no API) before every page grade:
      flat (std <= 5) unpainted-looking (light/greyish or near-black) band deeper than 8%
      of the height or width fails the attempt and skips the grade call. Thresholds are
      guesses from synthetic images; check them on the real p15/p16 PNGs. Possible false
      positives: a perfectly flat white/pale-grey area painted edge to edge (e.g. a flat
      cloud floor in a low-poly style).
- [ ] The p15/p16 bands may come from the pre-generated PLATES (pages are told to keep
      the plate's scene). Run `find_blank_band` over the stored plates and regenerate any
      that have a band (`scripts/pregenerate_assets.py --force` for those pages); the
      quiet-zone rules (plates included) now say to paint the area, never a blank strip.
- [x] Grader speed knobs: `GRADER_THINKING_LEVEL` (default `minimal`; google-genai 3.0
      `ThinkingLevel` = MINIMAL, LOW, MEDIUM, HIGH; falls back to LOW once if the model
      400s on the level) and grader input downscaled to `GRADER_IMAGE_MAX_SIDE` (768 px,
      JPEG q85). `GEMINI_VISION_MODEL` still selects the model (default unchanged).
- [ ] **Measure the latency effect on a real run**: grade-call duration (was ~10.0 s at
      LOW thinking with full-size inputs) with `minimal` + 768 px, and whether minimal
      thinking makes the grades noisier (more false passes / fails). Compare a run with
      `GRADER_THINKING_LEVEL=low`.
- [x] Time budget: `QUALITY_TIME_BUDGET_S` (45 s from the job start) stops further
      generations for failing pictures (best attempt kept, running generations finish;
      hero always >= 2 generations); recorded in `page_attempts.budget_stopped`
      (migration `0007`) and `budget_stopped` in SSE `hero`/`page` events plus an
      `attempt` event with phase `stopped`. `BOOK_TIME_TARGET_S` (60 s) logs slow books.
- [ ] UI: show `budget_stopped` (e.g. "kept the best of N - time limit") in app/web.
- [ ] With the budget, a book whose hero alone takes > 45 s gets no page retries at all;
      worth logging how often `budget_stopped` fires and on which pages.
- [ ] Small key objects in a text zone (p12 golden egg, p19 bean) still fail placement
      and cost a retry each; consider letting objects under a minimum size count as
      "move the text" only, or adding them to the quiet-zone instruction of those slots.

### Red sprite16 (61 s, $1.11) + Jack n64 (57 s, $1.16) real runs - fixes, unverified
All tested with fakes only (`tests/test_cast_costume_expression.py`); nothing ran on Gemini.
- [x] **Blurred bands** (Red p10/p12): `bands.py` also flags a blurred or smeared strip at
      the top/bottom (low detail vs. the middle, straight boundary across the width, blotchy
      colour inside). Smooth/ragged skies, fog, a soft picture as a whole pass (synthetic
      tests only). Root causes found in OUR prompts: slot-10/12/19 quiet zones asked for an
      "out-of-focus" top fifth and Red p10/p12, Jack p10/p12 art asked for "softly blurred"
      backgrounds - all reworded ("painted crisply ... not blurred"). Our code never pads or
      resizes a returned picture (PDF cover-crops). New: every Gemini image call logs its
      raw size and warns when the aspect is not 3:4 (+ reference sizes) - check the logs:
      if returned images ARE 3:4, the bands are composed by the model (prompt-driven);
      hypotheses: the plain light backgrounds of hero/family/cast refs (hero ref line now
      says not to copy it) and the off-ratio photo. Grader: blank/blurred/padded/
      letterboxed band = contradiction. **Unverified**: thresholds on real PNGs; a soft
      cloudy sky above a perfectly straight horizon may false-positive.
- [x] **Padding seams (confirmed root cause)**: stored pages are the right 896x1200, but the
      model paints a squarer picture inside and pads top/bottom (blurred zoomed copy, flat
      wall/white, extra sky) behind a straight full-width seam. New `find_seam_band`
      (before the blurred fallback, whose minimum depth is now 15%); flat-band "paper"
      saturation 0.30 -> 0.22 (a flat pale-blue sky was flagged). On the 60 exported real
      pages: 13/13 padded pages caught (red 1116 p1 bottom, p2, p10, p11, p12, p13; jack
      1116 p16, p20; jack 1111 p11, p12, p14, p15, p20), 0 of 45 good pages flagged; also
      flagged: red p14 top (plain ceiling) and jack 1116 p10 (dark cupboard bars the slot-10
      prompt asks for) - judged uncertain. Thresholds are fitted to these 60 pages (small
      margins: red p1 top ratio 0.45 vs a limit at 0.5 with detail > 2); re-check on new
      books. Known limit: a smooth sky above a dead-straight full-width cut is flagged. The
      page task line now names the tall 3:4 shape first. No real images in the repo (child
      likeness); tests synthesise the three pad types and legit horizontals.
- [x] **Costumes per page**: `costumes:` on characters; Red wolf plain on 2-3/18, Grandma's
      nightcap + shawl on 7-15 (`grandma_disguise`), cap flying off on 17 (`cap_flying`,
      uses the plain sheet). Grandma "never wears a nightcap". Prompts + grader per page.
- [x] **Prop counts / states**: `count: 1` (basket, whistle, nightcap, broom, axe, wardrobe,
      cottage, beanstalk, castle); `states:` per page (Jack beans p2/p3 colour placement,
      beanstalk chopped p17 / falling + stump p18 / stump 19-20). Always-on ONE_EACH_RULE.
      Red p1 (one basket), p11, p13, p14 and Jack p17/p18 rewritten as one moment via the
      new `art_caption` (the reader-facing `caption` is shown in the web viewer and is
      unchanged). Wrong prop state = contradiction.
- [x] **Supporting sheets + size**: `size:` on every non-hero character; lazily generated
      sheets per story x style x costume (`app/pipeline/cast_sheets.py`, kinds `cast_sheet`
      / `cast_sheet_check`, migration `0009` adds `character_bible.costume_id` - NOT applied
      here; run `alembic upgrade head`). Max 2 per page; grader `cast_match` hard-fails.
      Sheets are drawn with the first book's tier model and reused by both tiers.
- [x] **Expressions**: `expression:` on all 40 pages (Red 7/11 scared, Jack 9 frightened,
      15 alarmed ...); HERO_EXPRESSION_RULE; hero ref is neutral; CONSTRAINTS allow fear;
      grader `expression_match` (hard fail, but no budget exception).
- [x] **Budget exception**: `QUALITY_HARD_CAP_S` (70): one extra generation after the budget
      for a definite error (contradiction/duplicate, band, identity/family/cast mismatch).
- [x] **429s**: out of credit (the real cause: prepaid balance ran out) = 429 "exceeded your
      current quota" without RetryInfo / per-minute metric -> not retried, the book stops
      starting provider calls and keeps best attempts, ERROR "OUT OF CREDIT". 429 with
      RetryInfo or a per-minute metric -> retried after the delay / 2-4-8 s backoff,
      `IMAGE_RATE_LIMIT_RETRIES` (3), never past the hard cap. Optional
      `IMAGE_MAX_PER_MINUTE` (default off). Concurrency default unchanged (20).
- [ ] Measure on a real book: false-fail rates of `cast_match` / `expression_match` /
      blurred-band check, whether the sheet's grey bar leaks into pages, grade latency with
      up to 6 images, and the first-book delay from sheet generation.
- [ ] Slow image calls (16.7 s vs 9.6 s): `IMAGE_CONCURRENCY` (20) is not the bottleneck
      for one book (<= 20 page calls in flight). Suspects: provider latency with 20 parallel
      requests and bigger inputs (3-5 refs, the hero/family PNGs are ~1-2 MB each per
      call). Compare `api_calls.duration_s` vs `input_tokens`; consider sending refs as
      JPEG q90.
- [ ] Clean Cost-Efficient book estimate ~ $0.95-1.00 (22 images ~$0.77 incl. input,
      ~22 grades ~$0.20), ~50-55 s; every retry ~+$0.045. The $0.99 target needs near-zero
      retries.

### Adult photo: Jack sprite16 (90 s, $1.80, 39 page calls) + Red n64 (87 s, $1.73, 56) - fixes, unverified
Fakes only (`tests/test_child_version_tokens_cap.py`); nothing ran on Gemini.
- [x] **Hero identity collapse** (elderly bearded man in a suit; pages alternated between
      him and random children): photo traits now also return age group, glasses,
      headwear, facial hair, hair covered. A teen/adult/elderly photo is drawn as a CHILD
      VERSION (7-9, same face/skin/hair as photographed, grey stays grey, same glasses; no
      beard, wrinkles or adult clothes); glasses and headwear are kept on every page (hood
      over/around headwear); the hero always wears the story outfit. Pages no longer get
      the photo (hero reference = identity anchor); `HERO_CHILD_RULE` + per-book items line
      in every page prompt. Grader: likeness vs. the HERO REFERENCE; new definite checks
      `hero_child_ok` / `hero_items_ok` / `hero_outfit_ok` on the hero reference (loop
      regenerates it) and every page (another character's clothes = definite). Hair checks
      ignore hair covered by headwear. The form age is About-Me text only (no prompt).
- [x] **429 input tokens/min**: refs downscaled to 768 px JPEG + per-part
      `media_resolution` (first high, rest medium; 400 -> dropped once per model);
      token-aware limiter `IMAGE_INPUT_TOKENS_PER_MINUTE` (90k) learns from `in_tok`; every
      call logs `est_in_tok` / `in_tok` / window.
- [x] **Hard cap overrun** (cap 70 s, books 87-90 s): the cap only gated the START of the
      definite-error extra and the END of a rate-limit wait; retries before the budget
      were never checked. Now nothing optional starts unless image + grade (learned per
      book) ends before the cap; only a picture with no image may pass it.
- [x] Giant / giant's wife "at least ~2.5x, often cut off"; grader size fails only when a
      giant is < ~2x or a small one is huge. Jack p18 `stump` prop (count 1); p3 bean
      scene simplified (art fields); wolf `sheet_description` + one gentle retry on a
      PROHIBITED_CONTENT / SAFETY sheet refusal; seam pages (Red 10/11/14/15, Jack
      11/12/14) and slots 10/11/12/14/15 rewritten as tall compositions; a padded page
      gets `VERTICAL_RESCUE_RULE` on its last attempt (or after two padded attempts).
- [ ] Verify on Gemini: does `gemini-3.1-flash-lite-image` accept per-part
      `media_resolution` (check the "rejected media_resolution" warning) and what does
      `in_tok` show per page (est vs. actual)? Does an adult photo now give a recognisable
      child with glasses / headwear on every page? False-fail rate of the hero look checks.
- [ ] Clean Cost-Efficient book re-estimate: ~$0.97 (22 output images $0.74, ~22 grades
      ~$0.21, image input ~$0.02 - input tokens were never the cost driver, only the 429
      driver). The real $1.7-1.8 came from 39-56 page calls; $0.99 still needs near-zero
      retries. Lever not taken: grader `media_resolution` medium (~-$0.03/book).

### Cost-Efficient (cap $0.97): Jack $0.87 / 76 s, Red $0.88 / 48 s, zero retries - fixes, unverified
Fakes only (`tests/test_retry_phase_rescue.py`); nothing ran on Gemini.
- [x] **Retry phase** (`QUALITY_RETRY_PHASE`, on): definite-error retries were skipped
      although both books ended ~$0.10 under the cap - they were decided while most
      pages' first picture + grade were still reserved at the pricing seed. Now every
      page's first attempt runs first; retries (and new speaker portraits) then wait in a
      priority queue (definite errors - 2nd attempts before 3rd, most failures / lowest
      score first - then portraits, then soft issues), are decided one at a time with the
      book's ACTUAL average image / grade cost and only in-flight retries reserved, and run
      in parallel. A head that does not fit while retries run waits for them instead of
      being skipped. Early start for local band failures and `moment` portraits only when
      the projection fits (`QUALITY_EARLY_BAND_RETRY`).
- [x] **Free strip rescue** (`STRIP_RESCUE`, depth <= 22%, aspect crop <= 30%): a padded
      strip (blank / blurred / seam, any edge, top AND bottom in two passes) is cropped
      away, re-cropped to 3:4, upscaled, band-checked and graded (`rescue_grade`, no image
      call); `page_attempts.rescued` (migration 0012 - not run). Grader: a main
      character's face cut off by the frame is a contradiction.
- [x] Red hood DOWN on every page (outfit, all hero instructions, items rule); hood up
      hiding the headwear = `hero_items_ok` false (definite). Red p17/p18 wolf runs AWAY
      (from behind, tiny near the horizon on p18); Red p14, Jack p15/p18 direction
      wording; grader contradiction for a fleeing character facing what it flees. Stored
      Red p17/p18 background plates (if any) still show the old wolf - regenerate them.
- [ ] Verify on Gemini: rescue false-pass rate (cut faces / odd crops at 2-pass rescues),
      wall time of the retry phase (first pass ~25-35 s + one retry cycle ~20 s in
      parallel), and whether the hood rule holds on Red p11.

## Story / layout
- [x] ~~Only pages 1-3 by default~~ - `BOOK_PAGES` defaults to `all` (also in render.yaml and
      .env.example); `1-3` / `1,2,3` still work for cheap runs.
- [ ] Speed is an open question: one full 20-page run still has to be measured
      (`scripts/time_book.py`). A single retry adds ~26 s (regenerate + re-grade) to that
      picture, and contradictions now fail pictures that used to pass, so retries may get
      more frequent; the hero now also waits ~2 s for the photo traits.
- [ ] Under 60 s per book is a target, not a measurement: with 20 pages in parallel the wall
      time is roughly hero loop + slowest page loop + PDF. A page that needs 3 generations
      (each ~10-20 s on Pro plus a few s of grading) can alone exceed 60 s. Measure with
      `scripts/time_book.py` and consider `PAGE_IMAGE_MODEL=gemini-3.1-flash-image`.
- [ ] Gemini rate limits with 20 concurrent image calls per book (and several books at
      once, `IMAGE_CONCURRENCY=20` is per process) are unknown; 429s use up attempts.
- [x] Little Red Riding Hood is wired into the API (`story=red`). Not tried with a real key.
- [x] ~~Family variant not matched~~ - one vision call estimates skin tone (1-6) and hair;
      `app/pipeline/family.py` maps the YAML variant texts with a fixed keyword table and
      picks the closest (fallback: first variant). Still no parent-facing override.
- [x] ~~Text zone choice is not image-aware~~ - `place_text()` uses the grader's boxes: the
      layout's zones in order, then a centred `middle` band as a last resort on every page;
      first fully clear zone wins, else the least overlap (faces x3) among acceptable zones.
      A zone is blocked if it touches a face or covers more than 20% of a key object; a
      blocked placement counts as a failed check and regenerates with a quiet-zone rule
      naming the item ("keep the golden harp out of the bottom fifth").
- [ ] Placement treats image and trim coordinates as the same space (both 3:4); the 3 mm
      bleed crop shifts them by about 1%.
- [ ] About Me reuses the plain full-body hero reference image (cropped near the top). The
      story's `about_me.hero_instruction` (waist-up, holding the hen / basket) and
      `background_note` are not generated.
- [ ] The cover is a template (theme colour, title, hero in a frame), not a generated
      cover illustration.
- [ ] `text_fields.dedication` ("This book is for") is in layouts.yaml but has no form field.
- [ ] Page text sometimes leaves a single short word on the last line ("For / one cow."). The
      wrapping is greedy and does not balance lines.
- [ ] Some story text quotes speech without quotation marks ("Five magic beans, said the old
      man."). Needs an editorial decision.

## PDF
- [ ] Names in scripts the body font (Andika) does not cover (CJK, for example) draw as empty
      boxes. The pixel title font falls back to Andika only when it is missing glyphs. No
      multi-font fallback chain yet.
- [ ] Nunito is only published as a variable font in google/fonts, and ReportLab cannot select
      a weight from it, so the body font is Andika Bold/Regular instead (both OFL, bundled).
- [ ] No page numbers, no colour profile / PDF/X output, no print-shop preflight. Trim and
      bleed boxes are set, but the PDF is RGB.
- [x] PDF build runs in a worker thread inside the background job (not the request).
- [x] Mixed narration + speech in the dialogue box: per-page `speech` spans (validated at
      load time against `text`, never changing it); quotes + a per-preset highlight colour
      (>= 4.5:1 contrast) in the PDF and the web viewer. Spans are marked on every speech
      page of both stories (sound effects like "Flump!" are not speech).
- [x] Speaker portraits (`app/pipeline/portraits.py`, migration `0010`): `portrait: true`
      pages ask the grader `visible_characters`; the portrait is drawn only when the chosen
      picture does NOT show the speaker (unknown = none). Supporting characters' portraits
      are cached per story x style x costume x expression (`character_portraits`); hero and
      family portraits are per book. Shown in the PDF and the web viewer.
- [x] Second portrait reason `portrait: moment` (line spoken before/after the pictured
      action): the portrait is shown EVEN IF the speaker is visible (RPG convention, on
      purpose), no grader gate / presence question; started right after the hero, beside
      the pages (`Book.start_moment_portraits`); missing/failed/hard-capped = box without
      one. `portrait: true` = `offscreen` (grader-gated, unchanged). New expression
      `angry`. Pages: Jack p3 mother (angry), p11 giant (angry), p17 hero (scared);
      Red p3 wolf (sly, plain costume). Red p7 stays "???" with no portrait. Extra cost
      per book ~ 1 image + 1 check per portrait (Jack up to 3, Red 1) at first use; the
      giant / wolf portraits are cached after the first book of a style. Not yet run
      against Gemini.
- [ ] Expressions are made lazily one at a time (only the one a book needs), not as a full
      set of four per character. Red p4 / p8 now have name tabs (hero / Grandma).

## API / product
- [x] Books are stored in PostgreSQL per user (books, book_pages, api_calls; PNG/JPEG/PDF
      as `bytea`) and survive restarts. Owner-only access; other users get 404.
- [x] Google sign-in (Authlib OIDC, `email_verified` required, user matched by `sub`).
      `/api/hw1/generate` and all book routes require login. Dev-only `POST /auth/dev-login`
      behind `DEV_LOGIN=1` + non-https.
- [x] Progress: background job + SSE with a DB snapshot on (re)connect, per-picture events,
      heartbeat pings. Stale queued/running books are marked failed on startup.
- [x] (M4a) Wallet ledger, Stripe test-mode top-ups (webhook + success-page confirm),
      refund requests with admin approval, `/admin` cost stats.
- [x] (M4b) Books and HW1 Quick Scenes charge the wallet before any provider call (402
      if short) and re-credit on failure, crash, cancellation or deleting a running book.
      The price is the chosen tier's ($3.99 Professional / $0.99 Cost-Efficient).
- [x] ~~Crashed books not re-credited~~ - on startup, after `recover_stale_jobs`,
      `recredit_failed_books()` re-credits every FAILED book's charge that has no recredit
      entry (reason "server restarted"; idempotent).
- [ ] Billing follow-ups: live-mode switch (currently refused on purpose), refund/dispute
      webhooks (`charge.refunded`, `charge.dispute.created`), receipts/emails, admin audit log,
      per-user daily spend cap.
- [ ] No per-user spend cap or
      daily limit. Only guard: one active book per user (409). `/api/hw1/generate` has no
      concurrency limit beyond login.
- [ ] Jobs live in the web process: single uvicorn worker only, a deploy/restart kills
      running books (they are marked failed, the user must start again; no resume / no
      "regenerate from stored photo" endpoint yet). Needs a real queue (or a DB-claimed
      worker) to scale past one process.
- [ ] Images and PDFs are stored as `bytea` rows. Fine for a class project; a 20-page 2K/4K
      book is tens of MB, so move blobs to object storage before real traffic. Render's free
      Postgres plan has a small size limit and expires - check the plan before the demo.
- [ ] Migration `0002` was checked on SQLite only (test diffs it against the models); it has
      not been run against a real PostgreSQL here. Run `alembic upgrade head` on the
      deployment DB and smoke-test once.
- [ ] No CSRF token: state-changing routes rely on the `SameSite=Lax` session cookie and on
      POST/DELETE (cross-site form posts do not carry the cookie). Revisit if the cookie
      policy changes.
- [ ] Google OAuth consent screen must be *In production* (or the instructor added as a test
      user) or the instructor cannot sign in.
- [ ] No admin view of `api_calls` / costs yet (`ADMIN_EMAILS` unused).
- [ ] `/api/hw1/generate` records api_calls only per whole run on failure (it does not know
      which of its hero/scene calls failed). The family match / family sheet calls (with a
      grader configured) are recorded one by one.
- [x] HW1 hero now waits for the photo traits and carries the same identity line as the
      book's hero (shared `hero_identity_prompt`). Still no quality loop in HW1. Its runs
      are not stored as books, so they do not appear in the per-tier cost averages.
- [ ] Photo is sent to the provider with no face detection or "is this a child photo"
      check, and there is no consent / data-retention statement in the UI.
- [ ] Privacy: the normalised child photo (`books.photo_jpeg`) is kept in the database for
      as long as the book exists so it can be regenerated later. It is deleted only when
      the user deletes the book. Needs a stated retention period (e.g. purge photos after N
      days), a "delete my data / account" route, and a note in the UI. Logs contain only
      user/book ids (no emails, photos or tokens).
- [ ] The frontend for HW2 (form fields, page previews, PDF link) is being built separately
      and is not integrated or tested end-to-end against this API.
