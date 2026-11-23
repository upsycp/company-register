# New Company Register

A team website that files every new private company from India's monthly MCA incorporation report into
33 categories, finds founders, and lets the team build shared lists and notes. Young D2C brands from the
team's D2CStory list sit alongside them, staged by Instagram reach.

```
Teammate uploads the MCA .xlsx ─► nightly GitHub job files the new companies, looks up founders ─► site shows them
       (site: "Add MCA file")          (pipeline/update.py)                                    (docs/index.html)
```

| Part | Where it runs | Folder |
|---|---|---|
| Website: sign-in, files, lists, notes, upload | GitHub Pages, free | `docs/` |
| Team sign-in, lists and notes, the private register file | Supabase, free tier | `supabase/schema.sql` |
| Nightly update: new MCA files + founder lookups | GitHub Actions, free | `pipeline/`, `.github/workflows/update.yml` |

**What's public and what isn't.** The repository (code) is public, because GitHub Pages is only free on public
repos. The data is not in it: the register, founders and D2CStory brands live in a private Supabase bucket
that only signed-in emails on the team list can read. `.gitignore` keeps `report/`, `site/`, every `.xlsx`,
`.csv` and `.json` out of git.

## One-time setup (about 20 minutes)

### 1. Supabase
1. Create a free account and a new project at supabase.com. Pick the **Mumbai** region.
2. Open **SQL Editor → New query**, paste all of `supabase/schema.sql`, replace `you@yourcompany.com` at the
   bottom with your team's emails (lowercase, one per line), and click **Run**.
3. **Authentication → Sign In / Providers → Email**: turn **off** "Confirm email". Only emails in the `team`
   table can see anything, and Supabase's built-in email only sends a few messages an hour.
4. **Project Settings → API**: note the **Project URL**, the **anon public** key and the **service_role**
   key. The service_role key is secret. Don't put it in the website, in git, or in a chat.

### 2. Connect the website
Put the Project URL and the anon key in `docs/config.js`. Both are safe to publish.

### 3. Upload the register (once, from this Mac)
```bash
.venv/bin/python pipeline/seed.py
```
It asks for the Project URL and the service_role key (typed hidden, never saved), then uploads the current
register and founder lookups to the private bucket.

### 4. GitHub
1. Create a free account and a new **public** repository, e.g. `company-register`. Leave it empty.
2. Push this folder (a first commit is already made):
   ```bash
   git remote add origin https://github.com/<you>/company-register.git
   ```
   ```bash
   git push -u origin main
   ```
3. **Settings → Pages**: "Deploy from a branch", branch `main`, folder `/docs`. The site goes live at
   `https://<you>.github.io/company-register/` within a minute or two.
4. **Settings → Secrets and variables → Actions → New repository secret**: add `SUPABASE_URL` and
   `SUPABASE_SERVICE_KEY` (the service_role key).
5. **Actions** tab → enable workflows → "Update the register" → **Run workflow** once to check it works.

### 5. Your own domain (optional)
In **Settings → Pages → Custom domain**, enter e.g. `register.yourcompany.com`, then add the CNAME record
GitHub shows at your domain registrar. Tick "Enforce HTTPS" once it's available.

### 6. Invite the team
Add their emails in Supabase: **Table Editor → team → Insert row**. Send them the link; the first time,
they click "First time here? Create your account" and choose a password.

## Monthly routine
1. Download **Master Details of Companies Registered** for the month from mca.gov.in → Data & Reports →
   Company/LLP Information → Incorporated Or Closed During The Month.
2. On the site, click **Add MCA file** and pick the .xlsx.
3. That night the job files the new companies (they appear the next morning) and starts looking up founders.
   It looks up founders for up to 45 minutes every night, newest and best-matching first, and retries
   companies with no directors yet every 10 days, since new companies take weeks to show up in public records.
   To run it straight away: Actions → "Update the register" → Run workflow.

## How companies are filed
`pipeline/categorize.py` scores each private company (Pvt Ltd or OPC) against every category using its
registered business activity (the NIC code inside the CIN) and words in its name, and files it in the best
match if the score clears that category's bar. Traditional-business words (traders, exports, infra…) count
against it. Founders come from public company pages on Credonex, then Falcon Ebiz; both are third-party sites
that can change or block automated requests. If lookups start failing, add a paid CIN API (Sandbox.co.in, Surepass,
Attestr) as the `DIRECTORS_API_URL` and `DIRECTORS_API_HEADERS` repository secrets; the job then uses it
whenever the free sources find nothing.

D2CStory brands come from the team's copy of their list. Their terms don't allow publishing it, so keep the
site behind sign-in.
