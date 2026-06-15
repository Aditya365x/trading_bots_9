# Push to GitHub — exact commands (Windows PowerShell)

Run these from the project root (`d:\trading_bots`). Use a **PRIVATE** repo —
this contains your strategies. `.env` is git-ignored and will NOT be uploaded.

## 1. Initialize & commit (local)
```powershell
cd d:\trading_bots

git init
git config user.email "strangeconjuror@gmail.com"
git config user.name  "BOTS_ARMY"

git add -A

# >>> SAFETY CHECK: this must print ONLY ".env.example" (never ".env") <<<
git ls-files | Select-String "\.env"

git commit -m "9 trading bots: framework + strategies + deploy"
```
If the safety check shows `.env` (without `.example`), STOP — do not push. Tell me.

## 2. Create the GitHub repo
**Option A — website (easiest):**
1. Go to https://github.com/new
2. Name: `trading_bots`  ·  Visibility: **Private**
3. Do **NOT** add a README, .gitignore, or license (the repo already has them).
4. Click *Create repository*, copy the URL it shows.

**Option B — GitHub CLI (if you install it):**
```powershell
winget install --id GitHub.cli
gh auth login          # follow prompts (browser)
gh repo create trading_bots --private --source . --remote origin --push
```
If you used Option B, you're done — skip step 3.

## 3. Connect & push (for Option A)
```powershell
git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/trading_bots.git
git push -u origin main
```
When prompted:
- **Username:** your GitHub username
- **Password:** a **Personal Access Token** (GitHub no longer accepts your real
  password). Create one at https://github.com/settings/tokens → *Generate new
  token (classic)* → check **repo** scope → copy and paste it as the password.

## 4. Future updates (after editing anything)
```powershell
git add -A
git commit -m "describe your change"
git push
```

## 5. On EC2 — pull and run
See **DEPLOY_AWS.md**. Short version once the repo exists:
```bash
git clone https://github.com/<YOUR_USERNAME>/trading_bots.git
cd trading_bots
chmod +x deploy/aws_setup.sh && ./deploy/aws_setup.sh   # installs docker
cp .env.example .env && nano .env                        # paste your keys
docker compose build && docker compose up -d
```
