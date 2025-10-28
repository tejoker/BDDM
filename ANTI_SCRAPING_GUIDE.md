# Anti-Scraping Guide

## Overview

AoPS and Tricki have anti-scraping protection. This document explains the strategies implemented and how to improve success rates.

---

## ✅ Implemented Techniques

### 1. **Realistic Browser Headers** 
```python
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36...',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9...',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    # ... more headers to mimic real browser
}
```
**Why**: Makes requests look like they come from a real browser

### 2. **Random Delays Between Requests**
```python
delay = random.uniform(2.0, 5.0)  # 2-5 seconds
await asyncio.sleep(delay)
```
**Why**: Mimics human behavior, avoids patterns that trigger rate limiting

### 3. **Exponential Backoff Retry Logic**
```python
for attempt in range(max_retries):
    wait_time = (2 ** attempt) * random.uniform(1, 3)
    # Retry with increasing delays: 2s, 4s, 8s...
```
**Why**: Handles temporary blocks gracefully, gives server time to "forgive"

### 4. **Connection Limiting**
```python
connector = aiohttp.TCPConnector(limit=1, limit_per_host=1)
```
**Why**: Only 1 concurrent connection at a time (very respectful)

### 5. **Graceful Error Handling**
- Returns empty list instead of crashing
- Tracks failed requests for monitoring
- Continues even if blocked

---

## 🚀 Advanced Techniques (Not Implemented Yet)

### 1. **Rotating Proxies**
Use proxy services to rotate IP addresses:

```python
PROXIES = [
    'http://proxy1.example.com:8080',
    'http://proxy2.example.com:8080',
    # ... more proxies
]

async with session.get(url, proxy=random.choice(PROXIES)):
    ...
```

**Services**:
- **Bright Data** (formerly Luminati): Premium, $500+/month
- **ScraperAPI**: $49+/month, handles proxies automatically
- **Oxylabs**: $99+/month
- **Free proxies**: Unreliable, often blocked

**Cost**: $50-500/month depending on volume

### 2. **Selenium/Playwright (Browser Automation)**
Control a real browser to execute JavaScript:

```python
from playwright.async_api import async_playwright

async with async_playwright() as p:
    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.goto(url)
    content = await page.content()
```

**Pros**:
- Executes JavaScript (required for some sites)
- Looks exactly like a human user
- Can solve simple CAPTCHAs

**Cons**:
- 10-20x slower than direct HTTP requests
- High memory usage (~100 MB per browser)

### 3. **CAPTCHA Solvers**
Services that solve CAPTCHAs for you:

- **2Captcha**: $2-3 per 1000 CAPTCHAs
- **Anti-Captcha**: Similar pricing
- **CapMonster**: Self-hosted option

**How it works**:
1. Your scraper encounters CAPTCHA
2. Send image to solving service
3. Human solvers solve it (30-60 seconds)
4. Get solution and continue

**Cost**: ~$0.002-0.003 per CAPTCHA

### 4. **Session Cookies Management**
Maintain login sessions if you have accounts:

```python
# Login once
async with session.post(login_url, data=credentials) as response:
    cookies = response.cookies
    
# Reuse cookies for subsequent requests
async with session.get(url, cookies=cookies) as response:
    ...
```

### 5. **Residential Proxies**
Use real residential IP addresses (harder to block):

- **Bright Data**: $15 per GB
- **Smartproxy**: $12.5 per GB
- **Oxylabs**: $15 per GB

**Why residential**: Websites trust home IPs more than datacenter IPs

---

## 📊 Current Status

### AoPS (Art of Problem Solving)
- **Status**: ❌ Blocked
- **Protection Level**: HIGH
- **Likely using**: 
  - IP-based rate limiting
  - User-Agent filtering
  - Behavioral analysis
- **Recommendation**: Need rotating proxies + browser automation
- **Alternative**: Manual collection or official API (if available)

### Tricki.org
- **Status**: ❌ Site appears down/inactive
- **Protection Level**: N/A (site may be archived)
- **Likely issue**: Site itself not responding
- **Recommendation**: Check if site is permanently down
- **Alternative**: Use Internet Archive (Wayback Machine)

---

## 💡 Recommended Approach

### Option 1: Use Official APIs (BEST)
- Check if AoPS has an official API
- Contact site owners for permission
- **Pros**: Legal, reliable, fast
- **Cons**: May be denied or require payment

### Option 2: Light Scraping with Current Implementation (FREE)
- Current implementation respects rate limits
- Collect small amounts over long periods
- **Pros**: Free, legal if respectful
- **Cons**: Very slow, may still get blocked
- **Success rate**: ~10-30%

### Option 3: Premium Scraping Service ($$)
- Use **ScraperAPI** or **Bright Data**
- They handle proxies, CAPTCHAs, retries
- **Cost**: $50-500/month
- **Success rate**: ~90-95%

```python
# Example with ScraperAPI
import requests

API_KEY = 'your_api_key'
url = 'https://artofproblemsolving.com/wiki/...'
scraperapi_url = f'http://api.scraperapi.com?api_key={API_KEY}&url={url}'

response = requests.get(scraperapi_url)
```

### Option 4: Browser Automation (Playwright/Selenium)
- Slower but more reliable than direct HTTP
- Can handle JavaScript-heavy sites
- **Cost**: Free (just CPU/memory)
- **Speed**: 10-20x slower
- **Success rate**: ~60-80%

---

## 🎯 Practical Recommendations for Your Use Case

Given your goal of collecting **large datasets** for ML training:

### 1. **Focus on working sources first**
- ✅ Stack Exchange (200/200 items working)
- ✅ MathOverflow (200/200 items working)
- ✅ ProofWiki (working, ~342 items limit)
- ✅ ArXiv FULL (working)
- ✅ nLab (working)
- ✅ Wikipedia (working)

**These 6 sources give you ~1.2M items!**

### 2. **For AoPS, consider alternatives:**
- **Math StackExchange** has many competition problems
- **AMC/AIME official websites** publish past problems
- **Project Euler**: 800+ solved problems with explanations
- **OEIS (Online Encyclopedia of Integer Sequences)**: Math problems with solutions

### 3. **For Tricki:**
- **Internet Archive**: May have archived version
- **Alternative**: Focus on ProofWiki's proof techniques section
- **Math StackExchange**: "Proof techniques" tag has ~10k Q&As

---

## 🔧 If You Want to Implement Advanced Techniques

### Install Playwright
```bash
./math/bin/pip install playwright
./math/bin/playwright install chromium
```

### Use ScraperAPI (Recommended)
```bash
./math/bin/pip install scraperapi-sdk

# In your code:
from scraperapi_sdk import ScraperAPIClient
client = ScraperAPIClient('YOUR_API_KEY')
result = client.get(url)
```

**Pricing**: $49/month for 100k API calls (enough for AoPS dataset)

---

## ⚖️ Legal & Ethical Considerations

### ✅ Generally OK:
- Public data with no login required
- Respectful rate limiting (our current implementation)
- For research/education purposes
- Proper attribution in your papers

### ⚠️ Gray Area:
- Using proxies to bypass blocks
- Automated CAPTCHA solving
- Large-scale commercial use

### ❌ Not OK:
- Ignoring robots.txt
- DDoS-level request rates
- Circumventing paywalls
- Selling scraped data

**Our implementation**: Follows best practices, respects rate limits, handles blocks gracefully

---

## 📈 Expected Success Rates

| Technique | AoPS | Tricki | Cost | Speed |
|-----------|------|--------|------|-------|
| Current (polite HTTP) | 0-10% | 0% | Free | Fast |
| + Rotating Proxies | 30-50% | 10% | $50/mo | Fast |
| + Browser Automation | 60-80% | 20% | Free | Slow |
| + CAPTCHA Solver | 90-95% | 20% | $100/mo | Medium |
| ScraperAPI (All-in-one) | 90-95% | 50% | $50/mo | Fast |

---

## 🚀 Next Steps

1. **Stick with working sources** (6 sources = 1.2M items)
2. **If you really need AoPS**:
   - Try ScraperAPI free trial (1000 requests)
   - Or implement Playwright (free but slower)
3. **Tricki**: Check Internet Archive or skip it
4. **Monitor results** with the tracking we added:
   - `Requests: X, Failed: Y` shows success rate

**Bottom line**: You already have 1.2M high-quality items from working sources. AoPS and Tricki would add maybe 10-20k more. Probably not worth the effort/cost!
