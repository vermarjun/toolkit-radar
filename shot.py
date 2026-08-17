import asyncio, sys
from playwright.async_api import async_playwright
async def main():
    theme = sys.argv[1] if len(sys.argv) > 1 else "dark"
    sel = sys.argv[2] if len(sys.argv) > 2 else None
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        p = await b.new_page(viewport={"width": 1280, "height": 1000})
        errs = []
        p.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        await p.goto("file:///Users/vermarjun/Desktop/ComposioAssignment/site/index.html")
        await p.evaluate(f"document.documentElement.setAttribute('data-theme','{theme}')")
        await p.wait_for_timeout(1000)
        out = f"/tmp/shot-{theme}{'-'+sel.strip('#') if sel else ''}.png"
        if sel:
            await p.locator(sel).scroll_into_view_if_needed()
            await p.wait_for_timeout(500)
            await p.locator(sel).screenshot(path=out)
        else:
            await p.screenshot(path=out, full_page=True)
        print(out, "console errors:", errs[:3])
        await b.close()
asyncio.run(main())
