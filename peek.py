"""Human-verification helper: scrape a doc page and show the parts that matter."""
import re, sys
import agent
from agent.search import scrape, search

def show(url, terms, width=340, limit=10):
    md = scrape(url, max_chars=60000)
    if not md.strip():
        print(f"!! empty: {url}"); return
    print(f"### {url}  ({len(md)} chars)")
    hits = 0
    for m in re.finditer("|".join(terms), md, re.I):
        s = max(0, m.start()-width//2)
        print("   …" + re.sub(r"\s+", " ", md[s:s+width]) + "…")
        hits += 1
        if hits >= limit: break
    if not hits:
        print(re.sub(r"\s+"," ",md[:1200]))

if __name__ == "__main__":
    mode = sys.argv[1]
    terms = ["oauth","api key","api token","personal access","bearer","basic auth",
             "authentication","contact sales","enterprise plan","request access",
             "apply for","app review","paid plan","free plan","mcp"]
    if mode == "url":
        show(sys.argv[2], terms)
    else:
        for r in search(" ".join(sys.argv[2:]), limit=6):
            print(f"- {r['url']}\n    {r['description'][:220]}")
