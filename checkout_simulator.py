#!/usr/bin/env python3
"""
Checkout Simulator - Uses agent-browser to simulate e-commerce checkout flow.
Identifies payment methods available at checkout.

Usage: python3 checkout_simulator.py <website_url>
"""

import subprocess
import sys
import os
import json
import time
import re
from typing import Optional

# Setup NVM environment
NVM_SETUP = 'export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"'

# Session name for this run
SESSION = f"checkout_{int(time.time())}"

# Screenshots directory
SCREENSHOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)


def run_browser_cmd(cmd: str, timeout: int = 30) -> tuple[bool, str]:
    """Run an agent-browser command and return (success, output)."""
    full_cmd = f'{NVM_SETUP} && agent-browser --session {SESSION} {cmd}'
    try:
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def get_snapshot(interactive_only: bool = True, compact: bool = True) -> str:
    """Get page snapshot (accessibility tree)."""
    flags = ""
    if interactive_only:
        flags += " -i"
    if compact:
        flags += " -c"
    success, output = run_browser_cmd(f"snapshot{flags}")
    return output if success else ""


def find_element_ref(snapshot: str, patterns: list[str]) -> Optional[str]:
    """Find element reference (@eN) matching any of the patterns."""
    for pattern in patterns:
        # Look for @eN followed by text matching pattern
        regex = rf'(@e\d+)[^\n]*{re.escape(pattern)}'
        match = re.search(regex, snapshot, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def analyze_checkout(url: str) -> dict:
    """
    Simulate checkout flow and identify payment methods.
    Returns a structured report.
    """
    report = {
        "url": url,
        "status": "started",
        "is_ecommerce": False,
        "has_cart": False,
        "checkout_reached": False,
        "payment_methods": [],
        "bnpl_competitors": [],
        "cart_button_found": False,
        "product_found": False,
        "screenshots": [],
        "errors": [],
        "raw_checkout_snapshot": ""
    }

    print(f"[1/6] Opening {url}...")
    success, output = run_browser_cmd(f'open "{url}"', timeout=60)
    if not success:
        report["errors"].append(f"Failed to open URL: {output}")
        report["status"] = "failed"
        return report

    time.sleep(3)  # Wait for page load

    # Take homepage screenshot
    screenshot_home = os.path.join(SCREENSHOTS_DIR, f"{SESSION}_home.png")
    run_browser_cmd(f'screenshot "{screenshot_home}"')
    report["screenshots"].append(screenshot_home)

    print("[2/6] Analyzing homepage...")
    snapshot = get_snapshot()

    # Look for e-commerce indicators
    ecommerce_patterns = [
        "carrello", "cart", "bag", "shopping", "acquista", "buy",
        "add to cart", "aggiungi", "shop", "prodotti", "products"
    ]

    for pattern in ecommerce_patterns:
        if pattern.lower() in snapshot.lower():
            report["is_ecommerce"] = True
            break

    if not report["is_ecommerce"]:
        print("    Site doesn't appear to be an e-commerce")
        report["status"] = "not_ecommerce"
        run_browser_cmd("close")
        return report

    print("[3/6] Looking for products...")

    # Try to find and click on a product
    product_patterns = [
        "prodotto", "product", "articolo", "item", "dettaglio",
        "scopri", "vedi", "view", "details"
    ]

    product_ref = find_element_ref(snapshot, product_patterns)

    # If no product link found, try clicking on an image or card
    if not product_ref:
        # Look for any clickable image or link that might be a product
        img_match = re.search(r'(@e\d+)\s+link\s+[^\n]*\.(jpg|png|webp)', snapshot, re.IGNORECASE)
        if img_match:
            product_ref = img_match.group(1)

    if product_ref:
        print(f"    Clicking on product: {product_ref}")
        run_browser_cmd(f"click {product_ref}")
        time.sleep(2)
        report["product_found"] = True

        # Get product page snapshot
        snapshot = get_snapshot()

        # Take product screenshot
        screenshot_product = os.path.join(SCREENSHOTS_DIR, f"{SESSION}_product.png")
        run_browser_cmd(f'screenshot "{screenshot_product}"')
        report["screenshots"].append(screenshot_product)

    print("[4/6] Looking for Add to Cart button...")

    # Look for add to cart button
    cart_patterns = [
        "aggiungi al carrello", "add to cart", "add to bag",
        "acquista", "buy now", "compra", "aggiungi", "add"
    ]

    snapshot = get_snapshot()
    cart_ref = find_element_ref(snapshot, cart_patterns)

    if cart_ref:
        print(f"    Clicking Add to Cart: {cart_ref}")
        run_browser_cmd(f"click {cart_ref}")
        time.sleep(2)
        report["cart_button_found"] = True
        report["has_cart"] = True
    else:
        # Try finding button with "cart" in it
        button_match = re.search(r'(@e\d+)\s+button[^\n]*(cart|carrello|bag|acquist)', snapshot, re.IGNORECASE)
        if button_match:
            cart_ref = button_match.group(1)
            print(f"    Clicking cart button: {cart_ref}")
            run_browser_cmd(f"click {cart_ref}")
            time.sleep(2)
            report["cart_button_found"] = True
            report["has_cart"] = True

    print("[5/6] Navigating to checkout...")

    # Look for checkout/cart link
    checkout_patterns = [
        "checkout", "cassa", "procedi", "proceed", "vai al carrello",
        "view cart", "carrello", "cart", "pagamento", "payment"
    ]

    snapshot = get_snapshot()
    checkout_ref = find_element_ref(snapshot, checkout_patterns)

    if checkout_ref:
        print(f"    Clicking checkout: {checkout_ref}")
        run_browser_cmd(f"click {checkout_ref}")
        time.sleep(3)

        # Sometimes there's a second step
        snapshot = get_snapshot()
        proceed_patterns = ["procedi", "proceed", "continua", "continue", "checkout"]
        proceed_ref = find_element_ref(snapshot, proceed_patterns)
        if proceed_ref and proceed_ref != checkout_ref:
            run_browser_cmd(f"click {proceed_ref}")
            time.sleep(2)

        report["checkout_reached"] = True

    print("[6/6] Analyzing payment methods...")

    # Get final snapshot at checkout
    snapshot = get_snapshot(interactive_only=False)
    report["raw_checkout_snapshot"] = snapshot[:5000]  # Limit size

    # Take checkout screenshot
    screenshot_checkout = os.path.join(SCREENSHOTS_DIR, f"{SESSION}_checkout.png")
    run_browser_cmd(f'screenshot "{screenshot_checkout}" --full')
    report["screenshots"].append(screenshot_checkout)

    # Identify payment methods from snapshot
    payment_keywords = {
        "visa": "Visa",
        "mastercard": "Mastercard",
        "maestro": "Maestro",
        "amex": "American Express",
        "american express": "American Express",
        "paypal": "PayPal",
        "stripe": "Stripe",
        "nexi": "Nexi",
        "satispay": "Satispay",
        "apple pay": "Apple Pay",
        "google pay": "Google Pay",
        "gpay": "Google Pay",
        "bonifico": "Bank Transfer",
        "bank transfer": "Bank Transfer",
        "contrassegno": "Cash on Delivery",
        "cod": "Cash on Delivery",
        "postepay": "PostePay",
        "carta di credito": "Credit Card",
        "credit card": "Credit Card",
        "bancomat": "Bancomat"
    }

    bnpl_keywords = {
        "klarna": "Klarna",
        "clearpay": "Clearpay",
        "afterpay": "Afterpay",
        "scalapay": "Scalapay",
        "alma": "Alma",
        "oney": "Oney",
        "pay in 3": "PayPal Pay in 3",
        "pay in 4": "Pay in 4",
        "paga in 3": "Pay in 3",
        "paga in 4": "Pay in 4",
        "pagamento rateale": "Installments",
        "rate": "Installments",
        "sella personal credit": "Sella Personal Credit",
        "pagolight": "PagoLight",
        "soisy": "Soisy",
        "cofidis": "Cofidis",
        "findomestic": "Findomestic"
    }

    snapshot_lower = snapshot.lower()

    for keyword, name in payment_keywords.items():
        if keyword in snapshot_lower and name not in report["payment_methods"]:
            report["payment_methods"].append(name)

    for keyword, name in bnpl_keywords.items():
        if keyword in snapshot_lower and name not in report["bnpl_competitors"]:
            report["bnpl_competitors"].append(name)

    # Also check URL for payment provider hints
    success, current_url = run_browser_cmd("get url")
    if success:
        url_lower = current_url.lower()
        if "stripe" in url_lower:
            report["payment_methods"].append("Stripe")
        if "paypal" in url_lower:
            report["payment_methods"].append("PayPal")
        if "adyen" in url_lower:
            report["payment_methods"].append("Adyen")

    report["status"] = "completed"

    # Close browser session
    run_browser_cmd("close")

    return report


def format_report(report: dict) -> str:
    """Format the report for display/Slack."""
    output = []

    output.append(":browser: *CHECKOUT SIMULATION REPORT*")
    output.append(f"• URL: {report['url']}")
    output.append(f"• Status: {report['status']}")

    if not report["is_ecommerce"]:
        output.append("\n:warning: Sito non riconosciuto come e-commerce (nessun carrello/prodotti trovati)")
        return "\n".join(output)

    output.append(f"\n:shopping_trolley: *E-COMMERCE ANALYSIS*")
    output.append(f"• E-commerce detected: {'Yes' if report['is_ecommerce'] else 'No'}")
    output.append(f"• Product page reached: {'Yes' if report['product_found'] else 'No'}")
    output.append(f"• Add to Cart found: {'Yes' if report['cart_button_found'] else 'No'}")
    output.append(f"• Checkout reached: {'Yes' if report['checkout_reached'] else 'No'}")

    if report["payment_methods"]:
        output.append(f"\n:credit_card: *PAYMENT METHODS DETECTED*")
        for pm in sorted(set(report["payment_methods"])):
            output.append(f"• {pm}")

    if report["bnpl_competitors"]:
        output.append(f"\n:warning: *BNPL COMPETITORS PRESENT*")
        for bnpl in sorted(set(report["bnpl_competitors"])):
            output.append(f"• {bnpl}")
    else:
        output.append(f"\n:white_check_mark: *No BNPL competitors detected*")

    if report["screenshots"]:
        output.append(f"\n:camera: *SCREENSHOTS*")
        for ss in report["screenshots"]:
            output.append(f"• {ss}")

    if report["errors"]:
        output.append(f"\n:x: *ERRORS*")
        for err in report["errors"]:
            output.append(f"• {err}")

    return "\n".join(output)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 checkout_simulator.py <website_url>")
        print("Example: python3 checkout_simulator.py https://www.example-shop.it")
        sys.exit(1)

    url = sys.argv[1]

    # Ensure URL has protocol
    if not url.startswith("http"):
        url = f"https://{url}"

    print(f"\n{'='*60}")
    print(f"CHECKOUT SIMULATOR - {url}")
    print(f"{'='*60}\n")

    report = analyze_checkout(url)

    print(f"\n{'='*60}")
    print("REPORT")
    print(f"{'='*60}\n")

    print(format_report(report))

    # Also output JSON for programmatic use
    print(f"\n{'='*60}")
    print("JSON OUTPUT")
    print(f"{'='*60}")
    print(json.dumps(report, indent=2, ensure_ascii=False))
