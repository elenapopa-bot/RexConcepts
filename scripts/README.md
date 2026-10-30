# Rex Concepts report updater

Pulls Rex Concepts metrics from Looker (Chrome session cookies) and publishes `rex_concepts_report.html` to [elenapopa-bot/RexConcepts](https://github.com/elenapopa-bot/RexConcepts) via the GitHub API.

The report layout stays as it is. Each run only replaces the numbers, the latest-month summary, and the period labels. A partial Looker result is refused and does not overwrite the live report.

This is the same mechanism as Hesburger RO: the Mac must be awake, unlocked, and Chrome logged into Looker. GitHub Actions cannot see that Chrome session, so the old Actions workflow is not used.

## Mac setup (same as Hesburger RO)

1. **Python 3.11+** with dependencies:

   ```bash
   pip3 install -r scripts/requirements.txt
   ```

2. **Secrets.** Reuse the Hesburger token file, or create a Rex one with the same `GITHUB_TOKEN`:

   ```bash
   cp ~/.hesburger_ro_env ~/.rex_concepts_env
   ```

   `~/.rex_concepts_env` contents:

   ```bash
   export GITHUB_TOKEN="ghp_..."
   ```

   If `~/.rex_concepts_env` is missing, the script reads `~/.hesburger_ro_env`.

3. **Install the script** (do not copy the HTML; each run reads the live report from GitHub):

   ```bash
   mkdir -p ~/scripts
   cp scripts/rex_concepts_update.py ~/scripts/rex_concepts_update.py
   chmod +x ~/scripts/rex_concepts_update.py
   ```

4. **Looker**: log in to [bolt.cloud.looker.com](https://bolt.cloud.looker.com) in Chrome on the Mac that runs the job.

5. **Run manually**:

   ```bash
   source ~/.rex_concepts_env
   python3 ~/scripts/rex_concepts_update.py
   ```

   Log: `~/scripts/rex_concepts.log`

## Cron (Europe/Bucharest)

```cron
0 13 * * 1  source $HOME/.rex_concepts_env && /usr/bin/python3 $HOME/scripts/rex_concepts_update.py
0 13 2 * * source $HOME/.rex_concepts_env && /usr/bin/python3 $HOME/scripts/rex_concepts_update.py
```

- **Monday 13:00** — refresh the last closed month (Looker sometimes restates it).
- **2nd of the month 13:00** — roll the newly closed month into the report.

If `~/.rex_concepts_env` does not exist, point both lines at `source $HOME/.hesburger_ro_env` instead.

## Easiest run on Mac (no Cursor)

1. In Finder, open the repo folder → `scripts` → double-click **`run_update.command`**.
2. If macOS blocks it: right-click → **Open** → **Open** again.
3. Log in to Looker in Chrome when asked, then press **Enter** in the Terminal window.

## Report

https://github.com/elenapopa-bot/RexConcepts/blob/main/rex_concepts_report.html
