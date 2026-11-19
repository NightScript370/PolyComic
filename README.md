# PolyComic

FastAPI web app: upload a child's photo, pick a retro video-game style (N64 polygonal,
PS1 low-poly, 16-bit sprite) and get a personalised 20-page storybook PDF
(Jack and the Beanstalk / Little Red Riding Hood).

Status: milestone 3 (backend): Google sign-in, books stored per user in PostgreSQL (they
survive restarts), background book jobs with per-picture progress over SSE, parallel page
generation, JSON logging. Milestone 4: full 20-page books (cover + 20 pages + About Me;
`BOOK_PAGES` can still shorten them for cheap tests), pre-generated character bible and
scene plates, a vision-graded quality loop (up to 3 generations per picture) and
face-aware text placement. The buyer picks a quality tier per book: **Professional**
($3.99, Pro image model) or **Cost-Efficient** ($0.99, a cheaper image model, the default);
the charge is re-credited if the book fails.

## Run locally

```
cp .env.example .env      # fill in values; never commit .env
docker compose up --build
```

App: http://127.0.0.1:7659 (health: `/healthz`). Postgres is only reachable inside the compose network.

## Sign-in (Google OAuth)

Every generation endpoint requires sign-in. Set it up once:

1. Google Cloud Console -> APIs & Services -> **OAuth consent screen**: user type
   *External*, app name, support email; scopes `openid`, `email`, `profile` (all
   non-sensitive, so no Google verification review is needed).
2. **Credentials -> Create credentials -> OAuth client ID -> Web application.**
   Authorized redirect URI: `<PUBLIC_BASE_URL>/auth/callback`, e.g.
   `https://polycomic.example.com/auth/callback` (and `http://localhost:7659/auth/callback`
   for local runs). It must match `PUBLIC_BASE_URL` exactly (scheme, host, port).
3. Put the client id/secret in `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, set
   `PUBLIC_BASE_URL`, and set `SESSION_SECRET`
   (`python -c "import secrets;print(secrets.token_urlsafe(48))"`). The app refuses to
   start without `SESSION_SECRET` unless `IMAGE_PROVIDER=fake` or `DEV_LOGIN=1`.
4. **Publishing status:** while the consent screen is in *Testing*, only the Gmail
   addresses listed under *Test users* can sign in.
   For the instructor to sign in with their own Gmail, either click **Publish app**
   (status *In production*) or add the instructor's address as a test user.

Users are matched by Google's stable `sub` id; only email, name and picture URL are stored.
The session cookie (`polycomic_session`, signed, 14 days, `SameSite=Lax`, `Secure` when
`PUBLIC_BASE_URL` is https) holds only the user id.

**Dev login (local only):** with `DEV_LOGIN=1` *and* a non-https `PUBLIC_BASE_URL`,
`POST /auth/dev-login` (optional form fields `email`, `name`) signs you in without Google,
e.g. `curl -c jar -X POST localhost:7659/auth/dev-login`. Default off; it answers 404
otherwise. Never enable it on a public server.

## Quality tiers

The buyer chooses a tier per book (form field `tier` on `POST /api/books`) and per Quick
Scenes run (`/api/hw1/generate`). Both tiers use the same vision grader, the same quality
loop (up to 3 generations per picture) and everything else; only the image model and the
price differ. `app/tiers.py` (`tier_config`) is the single place that resolves a tier.

| Tier (`tier`) | Image model (hero, family sheet, pages) | Price setting (default) |
|---|---|---|
| `professional` - "PROFESSIONAL", top-quality art, slower | `PROFESSIONAL_IMAGE_MODEL`; empty = the pre-tier behaviour: `GEMINI_IMAGE_MODEL` (`gemini-3-pro-image`) for hero + family sheet, `PAGE_IMAGE_MODEL` (default the same) for pages | `PRICE_PROFESSIONAL_CENTS` (399) |
| `cost_efficient` - "COST-EFFICIENT", good art, low price (**default**) | `COST_EFFICIENT_IMAGE_MODEL` (`gemini-3.1-flash-lite-image`) for all three | `PRICE_COST_EFFICIENT_CENTS` (99) |

- **`BOOK_PRICE_CENTS` overrides both tier prices when it is set.** Leave it empty (or
  unset) to use the per-tier prices. `BOOK_PRICE_CENTS=0` makes every book and run free -
  handy for local testing; the UI then says "free right now". Do not set it in production
  (`render.yaml` sets the two tier prices instead).
- To A/B the cheap tier, change only `COST_EFFICIENT_IMAGE_MODEL` (e.g. to
  `gemini-nano-banana-2.1`); both candidates are priced in `app/providers/pricing.py`.
- **Per-book cost cap** per tier: `COST_CAP_COST_EFFICIENT_USD` (0.97) and
  `COST_CAP_PROFESSIONAL_USD` (3.70); 0 disables it. Required calls (photo traits, hero,
  each page's first picture and grade, the family sheet) always run. Optional calls - a
  retry generation, the definite-error extra after the time budget, the family sheet
  regeneration, a speaker portrait or its regeneration, a rate-limit re-try of an optional
  call - run only if the book's estimated spend so far + the first pictures still pending +
  the call (image + its check) stays under the cap. Definite-error retries may use the
  whole cap; soft-issue retries leave 3 % and portraits 6 % free (portraits go first).
  First-time shared supporting-character sheets are recorded but not counted. Skips are
  logged and stored (`page_attempts.cost_stopped`, `books.cost_skips`, migration `0011`).
- An unknown `tier` answers 400; a short wallet answers 402 with the tier's `price_cents`.
- `books.tier` (migration `0006`) stores the tier; books made before tiers existed are
  labelled `professional` (they all used the Pro model). `api_calls.model` records the
  model of every call.
- Pre-generated bible sheets / plates are keyed by story + style only and are used as
  references by both tiers (they are made with `PREGEN_IMAGE_MODEL`).
- With `IMAGE_PROVIDER=openai` both tiers use `OPENAI_IMAGE_MODEL` and differ in price only.
- `GET /api/wallet` lists both tiers with their price and average API cost per finished
  book; `/admin` shows average / median cost and margin per tier, and each recent book's tier.

## Billing (Stripe test mode)

Each book costs its tier's price (see above; $3.99 / $0.99). Users add funds to a wallet at `/wallet`
(Stripe Checkout, presets $5/$10/$20 or custom $1-$100); the ledger (`wallet_entries`, integer
cents) records every top-up, charge, re-credit and refund. **Test mode only**: if
`STRIPE_SECRET_KEY` does not start with `sk_test_`, an error is logged and billing endpoints
answer 503.

1. In the Stripe dashboard switch to the sandbox / test mode and copy the keys into `.env`
   (never commit it): `STRIPE_SECRET_KEY=sk_test_...`, `STRIPE_PUBLISHABLE_KEY=pk_test_...`.
2. Set `ADMIN_EMAILS=you@example.com` (comma-separated, case-insensitive) to reach `/admin`
   (refund requests, API cost per book).
3. Webhook (optional locally, because the success page also confirms the payment):
   `stripe listen --forward-to localhost:7659/api/stripe/webhook`, then put the printed
   `whsec_...` in `STRIPE_WEBHOOK_SECRET`. In production add an endpoint
   `https://<host>/api/stripe/webhook` for the `checkout.session.completed` event.
4. Pay with the test card `4242 4242 4242 4242`, any future expiry, any CVC.

Top-ups are credited once per Checkout Session, whether the webhook or the success page
arrives first. Refunds: the user requests one for a top-up (up to the unspent balance), an
admin approves (Stripe refund + ledger entry) or rejects it at `/admin`. Migration: `0003`.

## API (M3)

All `/api/books*` routes and `/api/hw1/generate` need sign-in (401 `{"error": "Please sign in."}`).
Other users' books answer 404.

`POST /api/hw1/generate` (Quick Scenes: hero + "Climbing the beanstalk" + "Planting the last
bean"; form fields `photo`, `style`, `detail`, `tier`) makes one hero call and two scene
calls, with no retries or quality loop, using the tier's image model, and charges the
tier's price. When a vision grader is configured it also runs the book's family match and
family sheet (shared code), the hero waits for the photo traits (~2 s) and carries the
same identity line as the book's hero, and the planting scene gets the sheet so the mother
matches the child; otherwise it uses the default family variant. The response includes
`tier`.

Provider errors: a 429 from genuine rate limiting answers "The image service is busy right
now. Please try again shortly."; a 429 that means the key has no quota (quota limit 0, free
tier, billing not enabled) answers "Image generation isn't available right now." and logs
an ERROR naming the likely cause. Such a quota error is not retried by the quality loop.

| Method / path | Notes |
|---|---|
| `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout` | Google OIDC; logout returns 204 |
| `GET /api/me` | `{"user": {id, name, email, picture} \| null}` |
| `POST /api/books` | multipart: `photo`, `story` (jack\|red), `style`, `detail`, `name`, `pronouns`, `age`, `fav_color`, `fav_food`, `fav_game`, `fav_animal`, `about`, `tier` (professional\|cost_efficient, default cost_efficient; anything else 400). Charges the tier's price first. 202 `{book_id, events_url}`; 402 `{error, balance_cents, price_cents}` (the tier's price) if the wallet is short; 409 if you already have a book in progress |
| `GET /api/books/{id}/events` | SSE: snapshot from the DB first, then live events (see below); ping every 15 s |
| `GET /api/books` | array, newest first: `{book_id, story, title, hero_name, status, created_at, cover_thumb_url, pdf_url, est_cost_usd, tier}` |
| `GET /api/wallet` | `{balance_cents, currency, book_price_cents (= Cost-Efficient price, kept for old clients), default_tier, tiers: [{id, label, price_cents, avg_cost_per_book_usd, books_costed}], presets_cents, custom_min_cents, custom_max_cents, avg_cost_per_book_usd, books_costed}` (averages over finished books) |
| `GET /api/books/{id}` | summary + `stage`, `stage_label`, `error`, `page_numbers`, `hero_url`, `hero_thumb_url`, `hero_attempts`, `hero_scores`, `events_url`, `pages[]` (page events + `status`) |
| `GET /api/books/{id}/hero.png`, `/hero/thumb.jpg`, `/pages/{n}.png`, `/pages/{n}/thumb.jpg` | images (`Cache-Control: private`) |
| `GET /api/books/{id}/JackAndTheBeanstalk.pdf` (or `LittleRedRidingHood.pdf`) | PDF attachment, once `status` is `done` |
| `DELETE /api/books/{id}` | cancels a running job, deletes the book (`{"ok": true}`) |

SSE events (JSON `data`):

- `stage {stage, label, done, total}` - stage `hero` / `pages` / `binding` / `done`, labels
  "Building hero", "Painting pages", "Binding the book", "Done"; `done`/`total` count
  pictures for the whole book (hero + pages).
- `attempt {page, attempt, max_attempts, phase}` - live quality-loop progress, `page` 0 is
  the hero; `phase` is `generating`, `checking` or `retrying`. A `retrying` after a failed
  check adds `likeness`, `fidelity` (0..1) and `issues` (list of strings); a `retrying`
  after a failed generation adds `error: true`. Not repeated in the reconnect snapshot.
- `hero {thumb_url, image_url, attempts, scores}`
- `page {page, title, caption, text, zone, thumb_url, image_url, attempts, scores}` (sent as
  each page finishes, in any order). `attempts` = generations used (1-3); `scores` =
  `{likeness, fidelity, passed}` of the chosen attempt, or `null` if it was not graded.
- `done {pdf_url}`, `error {message}`.

After a reconnect the snapshot repeats what already happened, so clients should key pages
by `page`.

Jobs run as asyncio tasks inside the web process. Every provider call (image, grade,
family match, family sheet) is stored in `api_calls` (estimated cost, tokens, duration, ok/error) and
every generation in `page_attempts` (thumbnail, scores, issues, chosen). On startup, books
left `queued`/`running` by a previous process are marked failed ("Server restarted -
please try again") and their charges are re-credited.

## Generation pipeline (M4)

1. **Family match + family sheet + hero (concurrently).** One vision call
   (`GEMINI_VISION_MODEL`) estimates the child's skin tone and hair; a fixed table in
   `app/pipeline/family.py` picks the closest family variant (`fam_a`..`fam_d`) for Mother /
   Grandma. Right after it, one image call draws a **family reference sheet**: every family
   member of the story side by side, full body, in the book's style and variant, no hero
   (kind `family_sheet` in `api_calls`, stored in `books.family_sheet_png`). It is skipped
   when pre-generated bible sheets exist for the variant or no page shows a family member,
   has no quality loop, and a failure only logs an error. Meanwhile the hero reference is
   generated (the tier's image model) and graded against the photo. Every hero call, the
   first included, waits for the measured traits (~2 s) and spells them out ("The child
   has brown skin and curly black hair - keep exactly that."); if the traits call fails,
   the hero starts without them. The family sheet still runs beside the hero. The traits
   call also returns the photo's age group, glasses, headwear and facial hair: a teen or
   adult photo is drawn as a CHILD VERSION (about 7-9; same face, skin, hair colour and
   texture as photographed, same glasses; no beard, wrinkles or adult clothes), glasses
   and headwear are kept on every page, and the hero always wears the story outfit. The
   hero reference is graded for this too (`hero_child_ok`, `hero_items_ok`,
   `hero_outfit_ok`), and so is every page. The form's age field is About-Me text only.
2. **Pages (all concurrently, `IMAGE_CONCURRENCY` image calls in flight per process;
   optional `IMAGE_MAX_PER_MINUTE` start cap).** A Gemini 429 is classified from its error
   body (`google.rpc.QuotaFailure` metric / id / limit, `RetryInfo` delay, logged): a zero
   quota, free tier / billing hint or a per-day quota is permanent; anything else (a
   per-minute quota, or no details) is a rate limit and the call is retried after the
   server's delay or 2/4/8 s backoff with jitter, at most `IMAGE_RATE_LIMIT_RETRIES` (3)
   times and never past `QUALITY_HARD_CAP_S`. Every image call also goes through a
   token-aware limiter (`IMAGE_INPUT_TOKENS_PER_MINUTE`, default 90 000: estimated prompt +
   reference tokens, corrected by the API's usage) so calls queue instead of hitting
   Google's 100k input-tokens-per-minute 429; references are downscaled to
   `IMAGE_REF_MAX_SIDE` (768, JPEG) and sent with a per-part `media_resolution` (first =
   high, others = medium; see `app/providers/refs.py`).
   Pages start when the hero is done; pages with a family member also wait for the family
   sheet (it normally finishes first: ~3 s + ~15 s vs. ~15 s + ~10 s for the hero).
   References per page (no photo - the hero reference is the identity anchor): hero, the
   page's background plate, the family sheet (for
   family members without a bible sheet), bible sheets for family members in the chosen
   variant and - without a plate - the reference sheets of at most 2 supporting characters
   in that page's costume (with a plate they are already painted in). Capped at the
   provider's maximum (14 for Gemini 3 image models, `IMAGE_MAX_REFS` overrides), priority
   hero > plate > family sheet > characters > supporting sheets. Pages use the
   tier's page model. Without plates the page falls back to the prompt + reference sheets
   (a warning is logged).
   - **Supporting-character sheets** (`app/pipeline/cast_sheets.py`): every non-family
     character (the wolf, the woodcutter, the giant, the hen ...) gets one sheet per story
     x style x costume, generated lazily by the first book that needs it (beside the
     hero, one image call `cast_sheet` + one vision check `cast_sheet_check`, at most one
     regeneration while the time budget lasts) and stored in `character_bible`
     (`costume_id`, migration `0009`) for every later book. The sheet shows the
     character alone next to a plain grey bar as tall as the hero child; the YAML's
     `size` field states each character's size relative to the child. A per-process lock
     and an ON CONFLICT DO NOTHING insert keep two books from making the same sheet twice.
   - **Costumes per page**: a character's `costumes: [{id, pages, description, sheet}]`
     (Red's wolf wears Grandma's nightcap and shawl on pages 7-15, the cap flies off on
     17) - every page prompt and the grader say what the character wears on THAT page, or
     that it wears no costume.
3. **Quality loop.** Each picture is graded (likeness, fidelity, identity checks, text
   artifacts, contradictions, softer issues, and boxes for faces, character bodies and key
   objects). Recurring objects are story-level `props` in the YAML (Jack's "exactly five
   small magic beans ... one red, one yellow, one green, one blue, one purple"); a page
   lists the ones it shows (`props: [beans]`), and the image prompt ("Props (draw exactly
   as described, same on every page)") and the grader get the same text.
   - **Contradictions** are objective, checkable errors only: a wrong count; a wrong
     colour of something whose colour is specified; a missing must-be-visible item or
     required character; the wrong or an extra character; a cut-out/sticker look; a
     blank, empty, unpainted, blurred-out, smeared, padded or letterboxed band or border;
     the same character or a single object drawn twice (no before/after or
     motion-sequence copies); inset panels, comic borders, split panels or several
     images; text. Any one fails the picture, leads the next attempt's
     corrections and costs 0.15 in best-of-3 (at most 0.45). NOT contradictions (at most
     softer issues, which never fail a picture): framing or cropping, how much of
     something is visible beyond being recognisable, exact pose or grip, relative size
     unless the description gives a size and it is off by more than about 2x, motion
     state (toppling vs. standing) unless that is the page's main event, lighting and
     background details. The grader prompt (`app/providers/gemini_vision.py`) lists both
     with examples taken from a real book's false failures.
   - **Identity checks** compare the hero with the original photo: skin tone, hair colour
     and hair type (straight / wavy / curly / coily). Stylisation, age and lighting
     differences are fine; a mismatch fails the picture whatever its likeness score, with
     a concrete correction ("the hero's hair must be straight and black like in the
     photo"). Likeness is about identity, never the expression.
   - **Supporting characters** (`cast_match`): each one is compared with its sheet and
     its line (look, outfit on this page, size relative to the hero child); a clear
     mismatch fails the picture with a concrete correction.
   - **Expression** (`expression_match`): every page has an `expression` in the YAML
     (e.g. Red p7 / p11: scared - wide eyes, raised worried brows, open mouth, leaning
     back). Page prompts say the hero reference fixes identity, not the expression; the
     grader fails a clearly wrong emotion (smiling when scared is asked, scared when happy
     is asked).
   - **Blank-band check (local, no API).** Before the grade call, every page picture is
     checked for a near-uniform, unpainted-looking band along an edge
     (`app/pipeline/bands.py`): the picture is shrunk to 256 px, and lines are counted
     inwards from each edge while each line is flat (largest channel std <= 5 on 0-255)
     and within 8 per channel of the edge line's colour. A band deeper than 8% of the
     height (top/bottom) or width (left/right) whose colour looks unpainted (light and
     nearly grey/cream/white: value >= 0.70 and saturation <= 0.22, or almost black)
     fails the attempt at once with "paint the whole picture edge to edge; no empty band
     at the top" and **skips the grade call** (saves ~10 s and its cost). It ranks below
     every graded attempt. A flat saturated sky or lawn is not flagged; a perfectly flat
     white or pale-grey area painted edge to edge could be. Every quiet-zone rule in the
     prompts also says to paint that area as part of the scene ("never a blank strip or
     border"), because "plain sky / plain background" wording produced the bands.
     **Padding seams** (the main cause, seen on the stored pages of three real books, all
     the right 896x1200 size): the model paints a squarer picture inside the 3:4 canvas
     and fills the leftover top/bottom with a blurred zoomed copy, a flat wall / white
     strip or an unrelated sky, joined by a hard, straight, full-width seam. When no flat
     band is found, `find_seam_band` looks for a row in the outer 45% where most of 32
     column blocks jump sharply at once, with the strip beyond it much smoother than the
     picture just inside (calibrated on 60 real pages: 13/13 padded pages caught, 0 of 45
     good pages flagged; a smooth sky above a dead-straight full-width cut is flagged too).
     **Blurred bands**: as a fallback, the same check looks for a blurred or
     smeared strip across the top or bottom - low detail compared with the middle of the
     picture, a straight horizontal boundary across the width, and blotchy colour
     structure inside (a smooth sky, fog or a plain wall has uniform rows and passes; a
     sky above trees or hills has a ragged boundary and passes). It fails the attempt the
     same way ("no blurred, smeared or out-of-focus strip at the top").
   - **Pass rule.** A picture passes when there is no identity mismatch, likeness >=
     `QUALITY_MIN_LIKENESS` (pages, 0.6) / `QUALITY_MIN_LIKENESS_HERO` (hero, 0.6),
     fidelity >= `QUALITY_MIN_FIDELITY` (0.6), no contradictions, no text artifacts, no
     blank band, and some text zone is clear (step 4). Otherwise it is regenerated with
     the issues as corrections, up to `QUALITY_MAX_ATTEMPTS` (3) generations in total;
     then the best-scoring attempt is used.
   - **Time budget.** `QUALITY_TIME_BUDGET_S` (default 45 s), measured from the start of
     the book job. Before every further generation the loop checks the clock: once the
     budget has passed, a picture that fails its check does **not** start another
     generation - it keeps its best attempt so far (best-of-N as above) and logs it. A
     generation already running is allowed to finish and is checked. The hero is never
     cut below 2 generations (the first attempt plus one retry), because every page
     depends on it; a picture with no image yet (all generations errored) also still
     retries. The attempt count reported is the number of generations actually made.
     The stop is recorded as `page_attempts.budget_stopped` on the kept attempt and sent
     as `budget_stopped` in the SSE `hero`/`page` events (and an `attempt` event with
     phase `stopped`). With the budget not reached - every normal book - the spec is
     unchanged: up to 3 generations per picture. `QUALITY_TIME_BUDGET_S=0` disables the
     budget. **Exception for definite errors**: if the best attempt so far has a
     contradiction (duplicate, wrong count ...), a blank or blurred band or an identity
     mismatch (hero, family or supporting character), the picture may make ONE more
     generation after the budget, but only if it is expected to END (image + grade, learned
     per book) before `QUALITY_HARD_CAP_S` (default 70 s; 0 disables the cap). The same rule
     now applies to every retry, sheet regeneration, rate-limit wait and token queue; only
     a picture with no image yet may pass the cap. If that generation
     fails too, the loop stops as `budget_stopped`. `BOOK_TIME_TARGET_S` (60 s) only logs a warning when a book job takes
     longer.
   - **Grader speed.** `GRADER_THINKING_LEVEL` (default `low`; google-genai 3.0 has
     `minimal`, `low`, `medium`, `high`; a model that rejects the level with a 400 falls
     back to `low` once per process). The picture, hero and photo are sent downscaled to
     `GRADER_IMAGE_MAX_SIDE` (768 px, JPEG q85) when larger, at a per-image
     `media_resolution` of `GRADER_MEDIA_RESOLUTION_FIRST` (picture under check, `medium`)
     and `GRADER_MEDIA_RESOLUTION` (every reference, `low`); a model that rejects it is
     graded without it. An answer that is not valid JSON is asked again once. Another vision model can be
     tried with `GEMINI_VISION_MODEL` (default `gemini-3.8-flash`).
4. **Text placement.** The layout's allowed zones are tried in order, then a centred middle
   band; the first that no face or key object touches wins, else the least-overlapping
   acceptable one (faces count 3x). A zone touching a face or covering more than 20% of a
   key object fails the check and the picture is regenerated with that area kept clear.
   The middle band is also blocked by any character's **body** (grader boxes of kind
   `figure`), not just faces; the top and bottom bands may overlap feet or legs and keep
   the face/object rules. When the middle band is chosen only as the least bad of several
   blocked zones, the corrective prompt names the layout's first zone instead. Key-object
   boxes larger than `OBJECT_MAX_AREA` (0.30) of the image or wider than 85% of it are
   regions (a garden, the sky), not objects, and are ignored; faces and figures never
   are. Slot 10 (the full-face close-up, Jack and Red page 10) prefers the top zone:
   the face is kept in the middle band and close-ups tend to reach down to the bottom.
5. **PDF**: cover + 20 pages + About Me.

### Pre-generating the character bible and plates

Run once per story x style (and again after changing a story or style). Needs
`DATABASE_URL` (migrated: `alembic upgrade head`) and `GEMINI_API_KEY`:

```bash
.venv/Scripts/python scripts/pregenerate_assets.py --story all --preset all --dry-run   # counts + cost
.venv/Scripts/python scripts/pregenerate_assets.py --story jack --preset n64
.venv/Scripts/python scripts/pregenerate_assets.py --story all --preset all            # 180 assets
```

`--only bible|plates`, `--force` (regenerate existing). One API call per asset, no retries;
failed assets are listed and a re-run fills only the gaps. Model `PREGEN_IMAGE_MODEL`
(default `GEMINI_IMAGE_MODEL`), concurrency `PREGEN_CONCURRENCY` (4). Plates leave out the
hero and family members (their look depends on the book's variant); other characters are
drawn into the plates from their bible sheets. Once the bible exists for a family variant,
books skip their per-book family sheet call.

### Timing a real book

```bash
.venv/Scripts/python scripts/time_book.py photo.jpg --story jack --preset n64
.venv/Scripts/python scripts/time_book.py photo.jpg --story jack --preset n64 \
    --image-model gemini-3-pro-image --page-model gemini-3.1-flash-image
```

Prints per-stage wall-clock times (family match, family sheet, hero, pages, binding, total; target under
60 s) and calls/cost per kind and model; saves the PDF and PNGs under `tmp_out/`. Every call
is billed. `--pages 1-3` for a short run, `--no-grader`, `--no-assets`.

## Running without Docker

You need Python 3.12+ and PostgreSQL. The scripts create `.venv`, install requirements, load `.env`,
run `alembic upgrade head` and start uvicorn on 127.0.0.1. `DATABASE_URL` should use the
`postgresql+asyncpg://` scheme (`postgres://` / `postgresql://` are rewritten automatically).

### Windows

1. Install PostgreSQL from the official installer (https://www.postgresql.org/download/windows/).
2. Create the role and database (as the `postgres` superuser; use your own password):
   ```
   psql -U postgres -c "CREATE ROLE polycomic LOGIN PASSWORD 'choose-a-password'"
   psql -U postgres -c "CREATE DATABASE polycomic OWNER polycomic"
   ```
3. `Copy-Item .env.example .env` and set in `.env`:
   `DATABASE_URL=postgresql+asyncpg://polycomic:<password>@localhost:5432/polycomic`
4. Run:
   ```
   ./scripts/run_local.ps1 -Reload        # add -Dev to install dev requirements
   ./scripts/run_local.ps1 -Fake -Reload  # offline: IMAGE_PROVIDER=fake
   ```
   Other options: `-SkipMigrate`, `-Port 8001`. The port is `-Port`, else `PORT` from the
   environment / `.env`, else 7659; keep `PUBLIC_BASE_URL` (and the Google redirect URI) in sync.
   If PowerShell blocks the script, use
   `powershell -ExecutionPolicy Bypass -File scripts/run_local.ps1` or run `Unblock-File scripts/run_local.ps1`.

### Linux / macOS

Create the database as above, `cp .env.example .env`, then:
```
scripts/run_local.sh --reload          # also: --dev, --fake, --skip-migrate, --port N
```
The port is `--port N`, else `PORT` from the environment / `.env`, else 7659.

### VPS with systemd (behind an existing nginx)

1. Copy the code to `/opt/polycomic`, then as root run `scripts/setup_vps.sh`
   (Debian/Ubuntu; installs PostgreSQL, creates the `polycomic` user, DB and role, the venv,
   and enables `deploy/polycomic.service`). The DB password is prompted for, or taken from
   `POLYCOMIC_DB_PASSWORD`.
2. Fill in `/opt/polycomic/.env` (including the `postgresql+asyncpg://` `DATABASE_URL`), then
   `systemctl start polycomic`. Logs: `journalctl -u polycomic -f`.
3. Adapt `deploy/nginx.conf.example` (the app listens on 127.0.0.1:7659) and add TLS.

The unit runs a single uvicorn worker on purpose: book jobs and SSE progress live in process
memory and are not shared between workers (books themselves are in Postgres).

## Tests and lint

```
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
ruff check .
pytest -q
```

Tests do not need PostgreSQL: each test gets a fresh SQLite file via `aiosqlite`, and one test
runs the Alembic migrations on SQLite and diffs them against the models. The migrations
themselves are written for PostgreSQL (`bytea` / `json`).

## Deploy

- VPS + nginx: run `docker compose up -d`, then adapt `deploy/nginx.conf.example`
  (domain, TLS). The app binds to 127.0.0.1:7659 only. `/api/books/` is configured for SSE.
- Render: use `render.yaml` as a Blueprint; it creates a Docker web service and a Postgres DB.
  Fill in the secrets marked `sync: false` in the dashboard. Set `PUBLIC_BASE_URL` to the service URL
  and add `<PUBLIC_BASE_URL>/auth/callback` to the Google OAuth client. Render's
  `postgresql://` connection string is rewritten to `postgresql+asyncpg://` automatically.

## Secrets

`.env` and `homework-*.md` are gitignored and must never be committed.
