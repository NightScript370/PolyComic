# Review log

## Run 1 — 2026-11-22 — reviewer: self — browser: Claude in Chrome (fresh, never-funded Google account)
Total 74 (was —)
PDF 30/40 · UI 18/20 · Money 14/20 (15/21 scaled) · Engineering 12/20
Book 1 (Jack): upload 19:50:56.987 → PDF 19:52:21.538 UTC (84.6 s, page recorder)
Book 2 (Red):  upload 19:52:54.890 → PDF 19:53:46.130 UTC (51.2 s, page recorder)
Balance: 0.00 → empty-balance attempt refused ("Not enough funds. This costs $0.99 and your balance is $0.00."), stays 0.00 → fund 5.00 → book 1 4.01 → book 2 3.02 → book refund not offered (stays 3.02)
Avg cost per book: $1.36 Cost-Efficient (wallet page; sums every api_calls row incl. grades and retries) against a $0.99 price
Phone width tested: 396px (window would not resize; site loaded in a 400px same-origin iframe, no horizontal overflow, full upload form usable)
Not measured: card refund of the $5.00 top-up (the request click was blocked on the reviewer's side; the line scores 0 either way under ruling 2)
Rulings: Money scaled 21→20 rounding down; refund = one book's $0.99 (not offered → 0), card refund offered but not tested; strict <60 s on the first timed book; history "timestamp" means a time, not only a date; shared template means matching text zones page by page.

Line scores
- PDF: pages 4/4 (cover + 20 story + About in each, 22 pp) · shared template 2/4 (same chrome and cover/About layout, but text zones differ page by page, e.g. p3 top in Jack / bottom in Red, p7 top / bottom-right) · same child 4/8 · one style 0/4 · no text over faces 10/10 (all 44 outlined pages checked; cap not triggered) · rendered type 4/4 · legible 2/2 · story complete 2/2 · About Me 2/2
- UI: opens fast 3/3 · first-time clarity 3/3 · named-stage progress bar 3/3 ("Building hero…", "Painting pages…", "Binding the book…") · thumbnails as they finish 1/3 · balance visible 2/2 · history 2/2 · ~400px 2/2 · failure messages 2/2
- Money (of 21): funding 4/4 · $0.99 each time 4/4 · refused when short 3/3 · book refund 0/4 · history cause/amount/timestamp 0/2 (date only) · reconciles 1/1 (5.00 − 0.99 − 0.99 = 3.02) · avg cost tracked and compared 3/3
- Engineering: <60 s 0/6 · sign out/in re-download 4/4 (both PDFs 200 application/pdf, ~4.47 MB) · character bible in DB 3/3 (`character_bible`, app/db/models.py:225; read by `get_bible_png`, app/db/repo_assets.py:40) · regeneration loop visible 3/3 (per-tile "1/3" attempt badges, "×1 try" on finished pages) · to-do.md 2/2 · review-log.md 0/2 (did not exist before this run)
- Key scan: clean. No secret or publishable keys in the working tree, the full history (3 commits), or common.js / form.js / book.js / page HTML.

Changed since last run: first run.

Fix first:
1. First book took 84.6 s, over the 60-second limit (Engineering −6). The recorder shows 6/21 pictures at 19:51:42.8, then nothing until 21/21 at 19:52:18.5, so 15 pictures were held for about 36 s, probably in quality checks. Book 2 made it in 51.2 s, so the limit can be met, but not reliably.
2. Art style switches mid-book (PDF −4). Jack pp. 13, 17 and 19 and Red pp. 9, 16 and 17 are painterly 2D illustrations; every other page is low-poly 3D. The hero also changes between books (PDF −4): in Jack he has light curly hair and sidelocks, in Red dark shoulder-length wavy hair. Neither matches the photo's short hair, and both look about 8 years old, not 15. Jack p. 11's close-up face doesn't match p. 1 either. Pin style_match harder, or regenerate pages the grader flags as off-style.
3. No way to refund a book (Money −4). The only refund is "Request refund" on a top-up, which goes to an admin for a Stripe card refund. Add a per-book refund (a $0.99 credit back to the wallet) that shows in the header balance without a reload.
4. Thumbnails mostly arrive at the end (UI −2): 15 of 21 landed in the same 250 ms poll at 19:52:18 (book 1). Show each page as soon as its final attempt is chosen.
5. Transaction history shows only a date, no time (Money −2). Also, review-log.md was missing (Engineering −2, fixed by this entry), and text zones differ between the two stories on the same page number (PDF −2).

Not scored, but worth fixing: average API cost is $1.36 per Cost-Efficient book against a $0.99 price, so every book loses about $0.37 even though the per-book cost cap is $0.97 (to-do.md notes that shared first-time cast sheets aren't counted against the cap).
