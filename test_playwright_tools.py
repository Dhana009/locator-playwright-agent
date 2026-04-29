import asyncio
import json
from tools.playwright_automation import (
    browser_launch,
    page_navigate,
    dom_extract,
    locator_find,
    locator_validate,
    action_assert,
    browser_get_state,
    screenshot_take
)


async def test():
    print("\n=== TEST 1: Launch browser ===")
    result = json.loads(await browser_launch(
        headless=True,
        base_url="https://playwright.dev"
    ))
    print(result)

    print("\n=== TEST 2: Get state ===")
    result = json.loads(await browser_get_state())
    print(result)

    print("\n=== TEST 3: Navigate ===")
    result = json.loads(await page_navigate(
        url="https://playwright.dev"
    ))
    print(result)

    print("\n=== TEST 4: Extract DOM ===")
    result = json.loads(await dom_extract())
    print(f"Found {result.get('count', 0)} elements")
    if result.get('elements'):
        print("First 3 elements:")
        for el in result['elements'][:3]:
            print(" ", el)

    print("\n=== TEST 5: Find locator ===")
    result = json.loads(await locator_find(
        element_data={
            "tag": "a",
            "text": "Get started",
            "role": "link"
        }
    ))
    print(result)

    print("\n=== TEST 6: Validate locator ===")
    if result.get('found'):
        val = json.loads(await locator_validate(
            locator=result['locator']
        ))
        print(val)

    print("\n=== TEST 7: Assert visible ===")
    if result.get('found'):
        assertion = json.loads(await action_assert(
            locator=result['locator'],
            assertion="visible"
        ))
        print(assertion)

    print("\n=== TEST 8: Screenshot ===")
    result = json.loads(await screenshot_take(
        filename="test-screenshot.png"
    ))
    print(result)

    print("\n=== ALL TESTS COMPLETE ===")


asyncio.run(test())
import asyncio
from tools.playwright_automation import (
    browser_launch,
    page_navigate,
    dom_extract,
    locator_find,
    locator_validate,
    action_assert,
    browser_get_state,
    screenshot_take
)

async def test():
    print("\n=== TEST 1: Launch browser ===")
    result = await browser_launch(
        headless=True,
        base_url="https://playwright.dev"
    )
    print(result)
    
    print("\n=== TEST 2: Get state ===")
    result = await browser_get_state()
    print(result)
    
    print("\n=== TEST 3: Navigate ===")
    result = await page_navigate(
        url="https://playwright.dev"
    )
    print(result)
    
    print("\n=== TEST 4: Extract DOM ===")
    result = await dom_extract()
    print(f"Found {result.get('count', 0)} elements")
    if result.get('elements'):
        print("First element:", result['elements'][0])
    
    print("\n=== TEST 5: Find locator ===")
    result = await locator_find(element_data={
        "tag": "a",
        "text": "Get started",
        "role": "link"
    })
    print(result)
    
    print("\n=== TEST 6: Validate locator ===")
    if result.get('found'):
        val = await locator_validate(
            locator=result['locator']
        )
        print(val)
    
    print("\n=== TEST 7: Screenshot ===")
    result = await screenshot_take(
        filename="test-screenshot.png"
    )
    print(result)
    
    print("\n=== ALL TESTS COMPLETE ===")

asyncio.run(test())
