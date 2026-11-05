# lang: Python
# file: seo_validator.py
# target: Windows/Linux/Mac
# note: Free Keyword Validator using Google Autocomplete & SERP Scraping.
#       Integrates with problem-finder output. No API keys needed.

import requests
from bs4 import BeautifulSoup
import json
import time
import random
from urllib.parse import quote_plus

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9"
}

def get_autocomplete_suggestions(keyword):
    """Fetches Google autocomplete suggestions."""
    url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={quote_plus(keyword)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5)
        data = json.loads(resp.text)
        return data[1] # List of suggestions
    except Exception as e:
        print(f"[!] Autocomplete error for '{keyword}': {e}")
        return []

def check_serp_competition(keyword):
    """Checks number of results and top competitors."""
    url = f"https://www.google.com/search?q={quote_plus(keyword)}&num=10"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Extract result count (approximate)
        stats_div = soup.find('div', {'id': 'result-stats'})
        if stats_div:
            text = stats_div.get_text()
            # Parse "About X results" or just numbers
            import re
            match = re.search(r'([\d,]+)', text)
            if match:
                count_str = match.group(1).replace(',', '')
                try:
                    count = int(count_str)
                    return count
                except:
                    pass
        
        # If parsing fails, estimate based on presence of ads/results
        results = soup.find_all('div', class_='g')
        return len(results) * 100000 # Rough estimate if exact count missing
        
    except Exception as e:
        print(f"[!] SERP check failed for '{keyword}': {e}")
        return -1

def get_people_also_ask(keyword):
    """Extracts PAA questions for FAQ generation."""
    url = f"https://www.google.com/search?q={quote_plus(keyword)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Look for PAA container
        paa_container = soup.find('div', attrs={'data-async-context': lambda v: v is not None and 'related_questions' in v})
        questions = []
        
        if paa_container:
            items = paa_container.find_all('span', class_='qTFwAc')
            for item in items[:5]:
                q_text = item.get_text(strip=True)
                if q_text:
                    questions.append(q_text)
                    
        return questions
    except Exception as e:
        print(f"[!] PAA extraction failed: {e}")
        return []

def analyze_keyword(keyword):
    """Full analysis pipeline for one keyword."""
    print(f"\n[*] Analyzing: '{keyword}'")
    
    # 1. Demand Check (Autocomplete)
    suggestions = get_autocomplete_suggestions(keyword)
    has_demand = len(suggestions) > 0
    
    # 2. Competition Check (SERP Count)
    serp_count = check_serp_competition(keyword)
    
    # 3. Content Ideas (PAA)
    faqs = get_people_also_ask(keyword)
    
    # Scoring Logic
    score = 0
    verdict = ""
    
    if has_demand:
        score += 30
        print(f"   [+] Demand Found ({len(suggestions)} suggestions)")
    else:
        print(f"   [-] Low Demand (No suggestions)")
        
    if serp_count == -1:
        print(f"   [?] Competition Unknown (Blocked/Error)")
        score += 10 # Neutral
    elif serp_count < 100000:
        print(f"   [+] Low Competition ({serp_count:,} results)")
        score += 40
    elif serp_count < 1000000:
        print(f"   [=] Medium Competition ({serp_count:,} results)")
        score += 20
    else:
        print(f"   [-] High Competition ({serp_count:,}+ results)")
        score += 5
        
    if faqs:
        print(f"   [+] FAQ Opportunities Found ({len(faqs)})")
        score += 10
        
    if score >= 60:
        verdict = "GREEN LIGHT ✅ Build It!"
    elif score >= 40:
        verdict = "YELLOW LIGHT ⚠️ Proceed with Caution"
    else:
        verdict = "RED LIGHT ❌ Skip This Idea"
        
    return {
        "keyword": keyword,
        "score": score,
        "verdict": verdict,
        "demand_suggestions": suggestions[:5],
        "competition_est": serp_count,
        "faq_ideas": faqs
    }

def batch_analyze(keywords_list):
    """Analyzes a list of keywords from problem-finder."""
    results = []
    for kw in keywords_list:
        res = analyze_keyword(kw)
        results.append(res)
        time.sleep(random.uniform(2, 5)) # Polite delay to avoid IP ban
        
    # Sort by score descending
    results.sort(key=lambda x: x['score'], reverse=True)
    
    print("\n" + "="*40)
    print("FINAL RANKING:")
    print("="*40)
    for i, r in enumerate(results, 1):
        print(f"{i}. [{r['score']} pts] {r['keyword']} -> {r['verdict']}")
        
    return results

if __name__ == "__main__":
    test_keywords = [
        "mortgage affordability calculator",
        "room dimensions planner online",
        "floor plan area calculator",
        "renovation cost estimator free"
    ]
    
    final_report = batch_analyze(test_keywords)
    
    # Save to JSON for Astro build prompt
    with open("validated_keywords.json", "w") as f:
        json.dump(final_report, f, indent=2)
        
    print("\n[+] Results saved to validated_keywords.json")
