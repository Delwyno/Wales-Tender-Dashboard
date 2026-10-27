# Wales Tender Pipeline · Piblinell Tendrau Cymru

A bilingual dashboard of public-sector tenders across Wales. Businesses can search live and upcoming tenders, awarded contracts and frameworks by council, sector, buyer and industry code. The data refreshes itself every day at 12:00 UK time.

## What's in this folder

| File | What it does |
| --- | --- |
| `index.html` | The dashboard page |
| `data/data.js` | All tenders, awards and frameworks. Rewritten by the daily update |
| `data/geo.js` | Council boundaries for the map (does not change) |
| `data/new.json` | Just the tenders added at the last update (a small feed other tools can read) |
| `data/state.json` | When the update last ran and what it found |
| `scripts/update.py` | The daily update. Reads the Find a Tender API (and Sell2Wales where it can), keeps Welsh notices, tidies names and merges duplicates |
| `.github/workflows/update.yml` | Runs the update every day at 12:00 UK time, saves the new data and republishes the site |
| `.github/workflows/deploy.yml` | Publishes the site to GitHub Pages |

## Set it up (about 10 minutes, once)

1. **Create the repository.** On GitHub, click **New repository**. Name it `wales-tender-pipeline`, make it **Public**, and create it with no README.
2. **Upload the files.** On the new repository page, click **uploading an existing file**. Drag in everything from this folder, including the hidden `.github` folder and `.nojekyll`. Then click **Commit changes**.
   - On a Mac, press Cmd + Shift + . in Finder to show hidden files.
   - If the `.github` folder won't drag across, use the GitHub Desktop app instead, or create the two files under **Add file → Create new file** with the names `.github/workflows/update.yml` and `.github/workflows/deploy.yml`, then paste their contents in.
3. **Turn on Pages.** Go to **Settings → Pages**. Under **Build and deployment → Source**, choose **GitHub Actions**.
4. **Let the update save its data.** Go to **Settings → Actions → General**. Under **Workflow permissions**, choose **Read and write permissions** and save.
5. **Publish the first time.** Go to the **Actions** tab, choose **Publish site**, then **Run workflow**. After a minute or two, the site is live at `https://<your-username>.github.io/wales-tender-pipeline/`.
6. **Run the first update.** In **Actions**, choose **Daily tender update**, then **Run workflow**. The first run catches up on everything published since 14 October 2026, the date of the original exports. From then on it runs by itself every day at 12:00.

## How the daily update works

- GitHub runs the job at 11:00 and 12:00 UTC. Only the run that lands at 12:00 UK time goes ahead, so it stays at noon through the change to and from British Summer Time.
- It asks Find a Tender for every notice published or changed since the last run, with a day of overlap so nothing is missed.
- It keeps notices delivered in Wales (NUTS/ITL codes starting `UKL`/`TLL`) or published by Welsh buyers. A Welsh buyer is matched by name, by an address region starting `UKL`/`TLL`, or by a Welsh postcode (CF, LL, NP, SA, LD).
- New tenders get a **New** label and appear under the **New since last update** filter. Awards and contract notices close the matching tender and add the contract to Awarded contracts.
- Tenders whose deadline passed more than a week ago are removed. Contracts that ended more than three years ago are dropped to keep the page fast.
- Each run writes a summary on its page in the **Actions** tab, saying how many tenders were added, updated or closed. If the Find a Tender API is down, the run fails and GitHub emails you, and the site keeps the previous day's data.

You can run it by hand at any time from **Actions → Daily tender update → Run workflow**. Enter a number of days to look further back, for example `30`.

## Good to know

- **Frameworks and dynamic markets** come from the 14 October 2026 Find a Tender export. Their status (active, upcoming, closed) updates daily from their dates. New frameworks also appear through award notices.
- **Sell2Wales:** since the Procurement Act 2023 started (February 2025), Welsh notices are published on Find a Tender, so that is the main source. The Sell2Wales API is tried as a bonus. If it fails, the run carries on without it.
- **GitHub pauses scheduled jobs** in repositories with no activity for 60 days. The daily data commits count as activity, so this should not happen while it is running.
- **Saved searches, shortlists and the My business profile** are stored in each visitor's own browser. No accounts and no tracking.
- **Run it on your own computer** (Python 3.9 or newer, no extra packages): `python scripts/update.py --force`, then open `index.html`.

## Data sources

- [Find a Tender service API](https://www.find-tender.service.gov.uk/apidocumentation/1.0/GET-ocdsReleasePackages) (Open Government Licence)
- [Sell2Wales API](https://api.sell2wales.gov.wales/v1)
- [WPPN 006: Carbon Reduction Plans](https://www.gov.wales/wppn-006-decarbonisation-through-procurement-taking-account-of-carbon-reduction-plans-html)
