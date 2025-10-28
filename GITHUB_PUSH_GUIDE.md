# Pushing BDDM to GitHub - Step by Step Guide

## 1. Create GitHub Repository

1. Go to https://github.com/new
2. Repository name: `BDDM`
3. Description: `Base de Données Mathématiques - Mathematical content scraper with LaTeX proof extraction`
4. Choose: **Public** or **Private**
5. **DO NOT** initialize with README, .gitignore, or license
6. Click "Create repository"

## 2. Prepare Local Repository

```bash
cd /home/nicolasbigeard/math_scraper

# Initialize git if not already done
git init

# Add all files (respects .gitignore)
git add .

# Check what will be committed
git status

# Commit
git commit -m "Initial commit: BDDM mathematical content scraper

Features:
- 6 data sources (Stack Exchange, ProofWiki, Wikipedia, nLab, MathOverflow, ArXiv FULL)
- ArXiv FULL LaTeX proof extraction (downloads tar.gz and extracts theorem-proof pairs)
- Unified collection script (6 parameters)
- Data normalization with quality scoring
- ~500,000 potential ArXiv proofs from research papers
"
```

## 3. Connect to GitHub

Replace `YOUR_USERNAME` with your GitHub username:

```bash
# Add remote
git remote add origin https://github.com/YOUR_USERNAME/BDDM.git

# Verify
git remote -v
```

## 4. Push to GitHub

```bash
# Push to main branch
git branch -M main
git push -u origin main
```

If you get authentication errors, you may need to:

**Option A: Use Personal Access Token (recommended)**
1. Go to GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token with `repo` scope
3. Use token as password when prompted

**Option B: Use SSH**
```bash
# Generate SSH key if you don't have one
ssh-keygen -t ed25519 -C "your_email@example.com"

# Add to ssh-agent
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519

# Copy public key
cat ~/.ssh/id_ed25519.pub

# Add to GitHub: Settings → SSH and GPG keys → New SSH key
# Paste the public key

# Change remote to SSH
git remote set-url origin git@github.com:YOUR_USERNAME/BDDM.git
```

## 5. Verify on GitHub

Go to `https://github.com/YOUR_USERNAME/BDDM` and verify:
- ✅ All files are there
- ✅ README_GITHUB.md is displayed (rename it to README.md first!)
- ✅ Documentation files are visible
- ✅ `.gitignore` is working (no `samples_en/`, `math/`, `__pycache__/`)

## 6. Optional: Rename README

```bash
# If README_GITHUB.md is showing instead of README.md
mv README.md README_OLD.md
mv README_GITHUB.md README.md

# Commit and push
git add .
git commit -m "Use GitHub README as main README"
git push
```

## 7. Add Topics/Tags (on GitHub)

On your repository page:
1. Click the gear icon next to "About"
2. Add topics: `python`, `mathematics`, `scraping`, `arxiv`, `latex`, `dataset`, `machine-learning`, `proof-extraction`, `math-education`
3. Save changes

## 8. Optional: Add GitHub Actions (CI/CD)

Create `.github/workflows/test.yml`:

```yaml
name: Test Collection

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.13'
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      - name: Test small collection
        run: |
          python collect_samples.py 2 2 2 2 2 0
```

## 9. Clone Fresh Copy (Test)

```bash
# In a different directory
git clone https://github.com/YOUR_USERNAME/BDDM.git
cd BDDM
./install.sh
./math/bin/python collect_samples.py 5 5 5 5 5 2
```

## 10. Keep Repository Updated

```bash
# After making changes
git add .
git commit -m "Your commit message"
git push
```

## Common Commands

```bash
# Check status
git status

# See what changed
git diff

# View commit history
git log --oneline

# Create branch
git checkout -b feature-name

# Switch branch
git checkout main

# Pull latest changes
git pull origin main

# Undo last commit (keep changes)
git reset --soft HEAD~1

# Undo last commit (discard changes)
git reset --hard HEAD~1
```

## What Gets Pushed

✅ **Included:**
- Python scripts (`.py`)
- Documentation (`.md`)
- Configuration (`requirements.txt`, `install.sh`)
- Scrapers and utilities

❌ **Excluded (by .gitignore):**
- Virtual environment (`math/`)
- Data files (`samples_en/`, `samples_fr/`)
- Python cache (`__pycache__/`)
- IDE files (`.vscode/`, `.idea/`)
- Windows Zone.Identifier files

## Repository Size

- **Without data**: ~500 KB (just code and docs)
- **With small test data**: ~1-2 MB
- **Collected data**: Should NOT be pushed (use .gitignore)

For large datasets, consider:
- GitHub Releases (for distributing collected data)
- Zenodo or Figshare (for research datasets)
- Hugging Face Datasets (for ML training data)

## Troubleshooting

**Problem: "Repository not found"**
- Check remote URL: `git remote -v`
- Verify repository exists on GitHub
- Check authentication

**Problem: "Permission denied"**
- Use personal access token or SSH key
- Verify token/key has correct permissions

**Problem: "Large files detected"**
- Check `.gitignore` is working
- Remove large files: `git rm --cached large_file`
- Use Git LFS for large files (>100 MB)

**Problem: "Merge conflicts"**
```bash
git pull origin main --rebase
# Fix conflicts manually
git add .
git rebase --continue
git push
```

---

## Quick Command Reference

```bash
# Complete setup from scratch
cd /home/nicolasbigeard/math_scraper
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/BDDM.git
git branch -M main
git push -u origin main
```

That's it! Your BDDM repository is now on GitHub! 🎉
