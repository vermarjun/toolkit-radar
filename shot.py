import asyncio,sys
from playwright.async_api import async_playwright
async def main():
    theme = sys.argv[1] if len(sys.argv)>1 else "dark"
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        p = await b.new_page(viewport={"width":1280,"height":1400}, device_scale_factor=2)
        await p.goto("file:///Users/vermarjun/Desktop/ComposioAssignment/site/index.html")
        await p.evaluate(f"document.documentElement.setAttribute('data-theme','{theme}')")
        await p.wait_for_timeout(900)
        errs=[]
        p.on("console", lambda m: errs.append(m.text) if m.type=="error" else None)
        await p.screenshot(path=f"/tmp/page-{theme}.png", full_page=True)
        print("errors:", errs)
        await b.close()
asyncio.run(main())
