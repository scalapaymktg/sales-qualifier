#!/usr/bin/env python3
"""
Webhook server for HubSpot deal notifications.
Triggers Claude agent when new deals are created that match filters.
"""

import os
import re
import subprocess
import hashlib
import hmac
import logging
import requests
from flask import Flask, request, jsonify

# Load .env file if exists
from pathlib import Path
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

# Configure logging - both console and file
SCRIPT_DIR_LOG = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR_LOG, "webhook.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),  # Console
        logging.FileHandler(LOG_FILE)  # File
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_SCRIPT = os.path.join(SCRIPT_DIR, "run_agent.sh")
HUBSPOT_CLIENT_SECRET = os.environ.get("HUBSPOT_CLIENT_SECRET", "")
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
HUBSPOT_BASE_URL = "https://api.hubapi.com"

# Filters - only process deals matching these criteria
TARGET_PIPELINE_ID = "77766861"  # Sales Pipeline ID
TARGET_GENERIC_SOURCE = "Marketing - Interactions & Inbound requests"

# Slack Configuration
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "C0A9K3A9WA3")  # inbound-sql-qualifier channel

# SEMrush API Configuration
SEMRUSH_API_KEY = os.environ.get("SEMRUSH_API_KEY", "")

# SimilarWeb API Configuration
SIMILARWEB_API_KEY = os.environ.get("SIMILARWEB_API_KEY", "")

# Anthropic API for Haiku triage
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# Ollama API for web search (free with ollama.com account)
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "").strip()

# Ollama configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "gemma3:4b"

# Tavily API for web search (optimized for AI agents, 1000 queries/month free)
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()

# WebSearchAPI fallback (generous free plan)
WEBSEARCH_API_KEY = os.environ.get("WEBSEARCH_API_KEY", "").strip()

# De-duplication: track deal IDs that already received a Slack message
# Key: deal_id, Value: True (message sent)
slack_message_sent = {}


def _check_ollama() -> dict:
    """
    Health check for Ollama: verifies the server is running and gemma3:4b is available.
    Returns {"available": True/False, "model_loaded": True/False, "error": "..."}.
    """
    status = {"available": False, "model_loaded": False, "error": None}
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            status["available"] = True
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            # Match both "gemma3:4b" and "gemma3:4b-..." variants
            if any(OLLAMA_MODEL in m for m in models):
                status["model_loaded"] = True
            else:
                status["error"] = f"Ollama attivo ma modello '{OLLAMA_MODEL}' non trovato. Modelli disponibili: {models}. Esegui: ollama pull {OLLAMA_MODEL}"
        else:
            status["error"] = f"Ollama ha risposto con status {resp.status_code}"
    except requests.exceptions.ConnectionError:
        status["error"] = f"Ollama non raggiungibile su {OLLAMA_BASE_URL} - esegui: ollama serve"
    except Exception as e:
        status["error"] = f"Errore check Ollama: {e}"
    return status


def _get_browser_headers() -> dict:
    """
    Restituisce header HTTP completi per sembrare un browser reale.
    Riduce i 403 da siti con anti-bot detection (es. ufficiocamerale.it).
    """
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Cache-Control": "max-age=0"
    }


def _tavily_search(query: str, max_results: int = 5) -> list:
    """
    Cerca via Tavily API (ottimizzata per AI agents, 1000 query/mese gratis).
    Più affidabile di DuckDuckGo e progettata per web research.

    Args:
        query: query di ricerca (include già site: se necessario)
        max_results: numero max risultati da restituire

    Returns:
        Lista di dict con {"url": "...", "title": "...", "snippet": "...", "content": "..."}
    """
    if not TAVILY_API_KEY:
        logger.warning("[tavily] TAVILY_API_KEY non configurato")
        return []

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            headers={"Content-Type": "application/json"},
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",  # "basic" è gratis
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False
            },
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "snippet": item.get("content", "")[:300],  # Tavily include contenuto estratto
                "content": item.get("content", "")  # Potremmo usarlo invece di scraping!
            })

        logger.info(f"[tavily] Trovati {len(results)} risultati per: {query[:80]}")
        return results

    except Exception as e:
        logger.warning(f"[tavily] Errore durante ricerca: {e}")
        return []


def _websearch_api_search(query: str, max_results: int = 5) -> list:
    """
    Cerca via WebSearchAPI (fallback a Tavily, piano free generoso).

    Args:
        query: query di ricerca (include già site: se necessario)
        max_results: numero max risultati da restituire

    Returns:
        Lista di dict con {"url": "...", "title": "...", "snippet": "...", "content": "..."}
    """
    if not WEBSEARCH_API_KEY:
        logger.warning("[websearch] WEBSEARCH_API_KEY non configurato")
        return []

    try:
        # WebSearchAPI.ai endpoint (POST con Bearer token)
        response = requests.post(
            "https://api.websearchapi.ai/ai-search",
            headers={
                "Authorization": f"Bearer {WEBSEARCH_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "query": query,
                "maxResults": max_results,
                "includeContent": False  # Non serve contenuto completo per trovare URL
            },
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        results = []
        # WebSearchAPI format: {"organic": [{"url": "...", "title": "...", "description": "..."}]}
        items = data.get("organic", [])

        for item in items[:max_results]:
            results.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "snippet": (item.get("description", "") or item.get("snippet", ""))[:300],
                "content": item.get("description", "") or item.get("snippet", "")
            })

        logger.info(f"[websearch] Trovati {len(results)} risultati per: {query[:80]}")
        return results

    except Exception as e:
        logger.warning(f"[websearch] Errore durante ricerca: {e}")
        return []


def _fetch_site_text(url: str, timeout: int = 10) -> str:
    """Fetch a URL and return cleaned text content (max 6000 chars)."""
    try:
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.skip = False
            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "noscript"):
                    self.skip = True
            def handle_endtag(self, tag):
                if tag in ("script", "style", "noscript"):
                    self.skip = False
            def handle_data(self, data):
                if not self.skip and data.strip():
                    self.text.append(data.strip())

        resp = requests.get(url, timeout=timeout, headers=_get_browser_headers())
        if resp.status_code != 200:
            return ""
        parser = _TextExtractor()
        parser.feed(resp.text)
        return " ".join(parser.text)[:6000]
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return ""


def _fuzzy_match_company_name(searched_name: str, found_name: str, threshold: float = 0.6) -> bool:
    """
    Verifica se due nomi azienda sono simili usando fuzzy matching.

    Args:
        searched_name: Nome cercato (es. "GRIVEL S.R.L.")
        found_name: Nome trovato nella pagina (es. "Grivel Srl")
        threshold: Soglia di similarità (0-1, default 0.6)

    Returns:
        True se i nomi sono simili (>= threshold), False altrimenti
    """
    import difflib
    import re

    # Normalizza: lowercase, rimuovi punteggiatura, forme giuridiche
    def normalize(name):
        name = name.lower()
        # Rimuovi forme giuridiche comuni
        name = re.sub(r'\b(srl|s\.?r\.?l\.?|spa|s\.?p\.?a\.?|snc|sas|ss|s\.?s\.?)\b', '', name, flags=re.IGNORECASE)
        # Rimuovi punteggiatura e spazi multipli
        name = re.sub(r'[^\w\s]', ' ', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    norm_searched = normalize(searched_name)
    norm_found = normalize(found_name)

    # Calcola similarità con SequenceMatcher
    similarity = difflib.SequenceMatcher(None, norm_searched, norm_found).ratio()

    logger.debug(f"[fuzzy_match] '{searched_name}' vs '{found_name}' -> similarity={similarity:.2f}")

    return similarity >= threshold


def _find_vat_in_html(html: str, searched_vat: str) -> bool:
    """
    Cerca il P.IVA nella pagina HTML.

    Args:
        html: HTML della pagina
        searched_vat: P.IVA cercato (può avere o non avere prefisso IT)

    Returns:
        True se P.IVA trovato nella pagina, False altrimenti
    """
    import re

    # Normalizza P.IVA: rimuovi IT prefix e spazi
    vat_clean = searched_vat.replace("IT", "").replace(" ", "").strip()

    # Pattern per cercare P.IVA nel testo (con o senza IT prefix)
    # Es: "P.IVA: 00139110076" o "IT00139110076" o "Partita IVA 00139110076"
    patterns = [
        rf'\b{vat_clean}\b',  # Numero esatto
        rf'\bIT\s*{vat_clean}\b',  # Con prefisso IT
        rf'(?:P\.?\s*IVA|Partita\s+IVA)[:\s]*{vat_clean}\b',  # Con label
    ]

    for pattern in patterns:
        if re.search(pattern, html, re.IGNORECASE):
            logger.debug(f"[vat_match] Found VAT {vat_clean} in HTML")
            return True

    return False


def _validate_multi_source_revenue(sources: list, hubspot_online: str = "", hubspot_offline: str = "") -> dict:
    """
    Valida coerenza tra valori fatturato di diverse fonti.

    Args:
        sources: Lista di dict {"source": str, "value": str, "confidence": str, "validated": bool}
        hubspot_online/offline: Revenue da HubSpot (opzionale)

    Returns:
        {
            "best_value": str,
            "best_source": str,
            "final_confidence": str,
            "validation_notes": list[str]
        }
    """
    import re

    def parse_revenue_to_number(value: str) -> float:
        """Converte string tipo '€ 1.234.567' in float 1234567."""
        if not value or value == "N/D":
            return 0.0
        # Remove € symbol and spaces
        cleaned = value.replace("€", "").replace(" ", "").strip()
        # Italian format: 1.234.567,89 -> 1234567.89
        cleaned = cleaned.replace(".", "").replace(",", ".")
        try:
            return float(cleaned)
        except:
            return 0.0

    notes = []

    # Parse tutti i valori
    parsed_sources = []
    for s in sources:
        num = parse_revenue_to_number(s["value"])
        if num > 0:
            parsed_sources.append({
                "source": s["source"],
                "value": s["value"],
                "number": num,
                "confidence": s.get("confidence", "N/D")
            })

    # Se non ci sono valori, ritorna N/D
    if not parsed_sources:
        return {
            "best_value": "N/D",
            "best_source": "",
            "final_confidence": "N/D",
            "validation_notes": ["Nessun valore trovato da nessuna fonte"]
        }

    # Se c'è solo una fonte, usa quella (con possibile downgrade se non validata)
    if len(parsed_sources) == 1:
        single_source = parsed_sources[0]
        final_conf = single_source["confidence"]
        validation_note = "Valore da singola fonte - non validabile con altre fonti"

        # CONFIDENCE DOWNGRADE: Se fonte unica + high confidence + non validata -> downgrade a low
        is_validated = sources[0].get("validated", False)  # Controlla flag validated dalla fonte originale
        if final_conf == "high" and not is_validated:
            final_conf = "low"
            validation_note = "⚠️ Confidence abbassato a LOW - valore da singola fonte non validato (nome/P.IVA non verificato)"
            logger.info(f"[validation] Downgrade confidence: single source '{single_source['source']}' not validated")

        return {
            "best_value": single_source["value"],
            "best_source": single_source["source"],
            "final_confidence": final_conf,
            "validation_notes": [validation_note]
        }

    # === MULTI-SOURCE VALIDATION ===
    # Ordina per confidence (high > medium > low) e poi per fonte più affidabile
    confidence_rank = {"high": 3, "medium": 2, "low": 1, "N/D": 0}
    parsed_sources.sort(key=lambda x: confidence_rank.get(x["confidence"], 0), reverse=True)

    best = parsed_sources[0]
    best_num = best["number"]

    # Confronta con altre fonti
    discrepancies = []
    agreements = []

    for other in parsed_sources[1:]:
        other_num = other["number"]
        # Calcola differenza percentuale
        if best_num > 0:
            diff_pct = abs(other_num - best_num) / best_num * 100
        else:
            diff_pct = 100 if other_num > 0 else 0

        if diff_pct < 10:
            # Valori simili (< 10% differenza) = conferma
            agreements.append(f"{other['source']} conferma valore simile (diff: {diff_pct:.1f}%)")
        elif diff_pct < 30:
            # Differenza moderata (10-30%) = warning
            discrepancies.append(f"⚠️ {other['source']} riporta valore diverso: {other['value']} (diff: {diff_pct:.0f}%)")
        else:
            # Differenza alta (>30%) = red flag
            discrepancies.append(f"❌ {other['source']} riporta valore molto diverso: {other['value']} (diff: {diff_pct:.0f}%)")

    # === CONFRONTO CON HUBSPOT (se disponibile) ===
    hubspot_values = []
    for label, hs_value in [("Online", hubspot_online), ("Offline", hubspot_offline)]:
        if hs_value and hs_value != "N/A":
            # HubSpot può avere range tipo "1M - 5M"
            if "-" in hs_value:
                # Prendi valore medio del range
                parts = hs_value.replace(" ", "").split("-")
                try:
                    low = parse_revenue_to_number(parts[0])
                    high = parse_revenue_to_number(parts[1])
                    hs_num = (low + high) / 2
                    hubspot_values.append((label, hs_num, hs_value))
                except:
                    pass
            else:
                hs_num = parse_revenue_to_number(hs_value)
                if hs_num > 0:
                    hubspot_values.append((label, hs_num, hs_value))

    for label, hs_num, hs_display in hubspot_values:
        if hs_num > 0:
            diff_pct = abs(hs_num - best_num) / max(hs_num, best_num) * 100
            if diff_pct < 30:
                agreements.append(f"HubSpot {label} ({hs_display}) è coerente (diff: {diff_pct:.0f}%)")
            elif diff_pct < 100:
                discrepancies.append(f"⚠️ HubSpot {label} ({hs_display}) è diverso (diff: {diff_pct:.0f}%)")
            else:
                discrepancies.append(f"❌ HubSpot {label} ({hs_display}) è molto diverso (diff: {diff_pct:.0f}%)")

    # === CALCOLA CONFIDENCE FINALE ===
    final_confidence = best["confidence"]

    # Upgrade confidence se ci sono conferme
    if len(agreements) >= 2 and not discrepancies:
        if final_confidence == "medium":
            final_confidence = "high"
            notes.append("✅ Confidence aumentato a HIGH - valore confermato da multiple fonti")
        elif final_confidence == "high":
            notes.append("✅ Valore confermato da multiple fonti coerenti")
    elif len(agreements) >= 1 and not discrepancies:
        notes.append("✅ Valore confermato da almeno un'altra fonte")

    # Downgrade confidence se ci sono discrepanze
    if len([d for d in discrepancies if "❌" in d]) >= 1:
        # Discrepanza alta (>30%)
        if final_confidence == "high":
            final_confidence = "medium"
            notes.append("⚠️ Confidence abbassato a MEDIUM - fonti riportano valori molto diversi")
        elif final_confidence == "medium":
            final_confidence = "low"
            notes.append("❌ Confidence abbassato a LOW - fonti riportano valori molto diversi")
    elif len([d for d in discrepancies if "⚠️" in d]) >= 2:
        # Multiple discrepanze moderate (10-30%)
        if final_confidence == "high":
            final_confidence = "medium"
            notes.append("⚠️ Confidence abbassato a MEDIUM - multiple fonti riportano valori diversi")

    # Aggiungi tutte le note
    notes.extend(agreements)
    notes.extend(discrepancies)

    return {
        "best_value": best["value"],
        "best_source": best["source"],
        "final_confidence": final_confidence,
        "validation_notes": notes
    }


def _vies_lookup(vat: str) -> dict:
    """Look up company name and address from VAT via EU VIES REST API.

    Returns: {
        "name": str,
        "address": str,
        "country_code": str  # ES, FR, IT, etc. from originalVatNumber
    }
    """
    # Normalize VAT: remove spaces, uppercase
    vat_clean = vat.replace(" ", "").upper()

    # Extract country code (first 2 chars if letters, else default to IT)
    if len(vat_clean) >= 2 and vat_clean[:2].isalpha():
        country_code = vat_clean[:2]
        vat_number = vat_clean[2:]
    else:
        # No country prefix - assume Italy (Scalapay's main market)
        country_code = "IT"
        vat_number = vat_clean

    try:
        resp = requests.get(
            f"https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{country_code}/vat/{vat_clean}",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("isValid"):
                # Extract country code from originalVatNumber (authoritative source)
                original_vat = data.get("originalVatNumber", "")
                detected_country = original_vat[:2] if len(original_vat) >= 2 and original_vat[:2].isalpha() else country_code

                return {
                    "name": data.get("name", ""),
                    "address": data.get("address", "").strip(),
                    "country_code": detected_country
                }
    except Exception as e:
        logger.warning(f"VIES lookup failed for {vat}: {e}")

    # Fallback: return country code based on VAT prefix (even if VIES failed)
    return {"country_code": country_code}


def _llm_extract_from_text(page_text: str, company_name: str, vat: str, result: dict) -> dict:
    """
    Last-resort LLM extraction: pass cleaned page text to gemma3:4b.
    Used only when regex patterns A/B/C all fail, but we ARE on the correct detail page.
    Much more reliable than search-page fallback because the text is company-specific.
    """
    # Check Ollama availability before attempting LLM extraction
    ollama_status = _check_ollama()
    if not ollama_status["available"] or not ollama_status["model_loaded"]:
        logger.error(f"[LLM detail-page] Ollama non disponibile: {ollama_status['error']}")
        result["ollama_offline"] = True
        return result

    prompt = f"""Dal testo seguente, estratto dalla pagina di bilancio dell'azienda {company_name} (P.IVA {vat}),
estrai SOLO il fatturato annuo (ricavi/revenue).

TESTO PAGINA:
{page_text[:3000]}

Rispondi SOLO con questo JSON (nessun altro testo):
{{
  "fatturato": "<importo in euro, es. '459.326' o '3.815.456' o 'N/D'>",
  "anno_bilancio": "<anno, es. '2024' o 'N/D'>"
}}"""

    try:
        ollama_resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": "Estrai dati finanziari. Rispondi in JSON valido."},
                    {"role": "user", "content": prompt}
                ],
                "stream": False
            },
            timeout=60
        )
        if ollama_resp.status_code == 200:
            content = ollama_resp.json().get("message", {}).get("content", "")
            clean = content
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0]
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0]
            import json as json_module
            parsed = json_module.loads(clean.strip())
            fat = parsed.get("fatturato", "N/D")
            if fat and fat != "N/D":
                result["fatturato"] = "€ " + fat if not fat.startswith("€") else fat
            anno = parsed.get("anno_bilancio", "N/D")
            if anno and anno != "N/D":
                result["anno_bilancio"] = anno
            logger.info(f"[LLM detail-page] Extracted: fatturato={result['fatturato']}")
    except Exception as e:
        logger.warning(f"[LLM detail-page] extraction failed: {e}")

    return result


def _fatturatoitalia_extract(company_name: str, vat: str) -> dict:
    """
    Build direct URL to fatturatoitalia detail page and extract fatturato via regex.
    URL pattern: /{slug}-{vat} where slug = lowercase name with underscores.
    """
    import re

    result = {
        "fatturato": "N/D",
        "anno_bilancio": "N/D",
        "utile_perdita": "N/D",
        "dipendenti": "N/D",
        "confidence": "N/D",  # high/medium/low - indica affidabilità del dato
        "diagnostica": ""
    }

    # Build slug from company name
    slug = company_name.lower().replace(".", "").replace(",", "")
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    detail_url = f"https://www.fatturatoitalia.it/{slug}-{vat}"

    logger.info(f"Fetching fatturatoitalia detail: {detail_url}")

    try:
        resp = requests.get(detail_url, timeout=10, headers=_get_browser_headers(), allow_redirects=True)
        if resp.status_code != 200:
            logger.warning(f"fatturatoitalia detail page returned {resp.status_code}")
            return result

        # Detect redirect to homepage (company not found)
        if resp.url.rstrip("/") == "https://www.fatturatoitalia.it" or resp.url.rstrip("/") == "https://fatturatoitalia.it":
            logger.warning(f"fatturatoitalia redirected to homepage - company not found")
            return result

        html = resp.text

        # --- Pattern A: meta description (most reliable) ---
        # Format 1: "fatturato 3.815.456 €, utile 78.167 € (2024)"
        # Format 2: "fatturato 21.323.834.620, utile e bilancio 2024"
        meta_m = re.search(
            r'content="[^"]*fatturato\s+([\d.,]+)\s*€?,\s*(?:utile|perdita)\s+([-\d.,]+)\s*€?\s*\((\d{4})\)',
            html, re.IGNORECASE
        )
        if not meta_m:
            # Variant without utile value, just "utile e bilancio YYYY"
            meta_m2 = re.search(
                r'content="[^"]*fatturato\s+([\d]{1,3}(?:\.[\d]{3})+(?:,\d{2})?)[^"]*?(\d{4})',
                html, re.IGNORECASE
            )
            if meta_m2:
                result["fatturato"] = "€ " + meta_m2.group(1).strip()
                result["anno_bilancio"] = meta_m2.group(2)
                result["confidence"] = "high"  # Pattern A meta = alta affidabilità
        if meta_m:
            result["fatturato"] = "€ " + meta_m.group(1).strip()
            result["utile_perdita"] = "€ " + meta_m.group(2).strip()
            result["anno_bilancio"] = meta_m.group(3)
            result["confidence"] = "high"  # Pattern A meta = alta affidabilità

        # --- Pattern B: body text 'sono pari a <b> 459.326  €</b>' ---
        if result["fatturato"] == "N/D":
            m = re.search(r"(?:sono pari a|fatturato di)\s*<b>\s*([\d.,]+)\s*€", html, re.IGNORECASE)
            if m:
                result["fatturato"] = "€ " + m.group(1).strip()
                result["confidence"] = "high"  # Pattern B con frase specifica = alta affidabilità

        # --- Pattern C: generic sweep - amount near fatturato/ricavi keywords ---
        if result["fatturato"] == "N/D":
            # Strip HTML tags for cleaner text matching
            text_only = re.sub(r"<[^>]+>", " ", html)
            text_only = re.sub(r"\s+", " ", text_only)
            # Must have dot-separated thousands (min X.XXX = 1000+) to avoid false positives
            # € symbol optional - the dot-thousands format is sufficient guard
            gc = re.search(
                r"(?:fatturato|ricavi).{0,80}?([\d]{1,3}(?:\.[\d]{3})+(?:,\d{2})?)\s*(?:€|euro)?",
                text_only, re.IGNORECASE
            )
            if gc:
                # VALIDAZIONE NEGATIVA: Verifica che non sia capitale sociale o altro
                candidate_value = gc.group(1).strip()
                match_start = gc.start()
                match_end = gc.end()
                # Estrai contesto (100 caratteri prima e dopo il match)
                context_start = max(0, match_start - 100)
                context_end = min(len(text_only), match_end + 100)
                context = text_only[context_start:context_end].lower()

                # Negative keywords che indicano che NON è fatturato
                negative_keywords = [
                    "capitale sociale", "capitale soc.", "cap. soc.", "cap sociale",
                    "patrimonio netto", "patr. netto", "patrimonio",
                    "debiti", "debito",
                    "attivo", "passivo",
                    "immobilizzazioni", "immob.",
                    "crediti", "credito"
                ]

                # Se il contesto contiene negative keywords, scarta questo valore
                has_negative = any(neg in context for neg in negative_keywords)
                if has_negative:
                    logger.warning(f"[fatturatoitalia] Valore {candidate_value} scartato - contesto negativo: {context[:80]}")
                    result["diagnostica"] = f"Valore trovato ma scartato (probabile capitale sociale/patrimonio)"
                else:
                    result["fatturato"] = "€ " + candidate_value
                    result["confidence"] = "medium"  # Pattern C ha confidence medio

        # --- Pattern D: LLM micro-extraction on detail page (last resort) ---
        if result["fatturato"] == "N/D":
            page_text = _fetch_site_text(detail_url)
            if page_text:
                result = _llm_extract_from_text(page_text, company_name, vat, result)

        # --- Anno from body if not found yet ---
        if result["anno_bilancio"] == "N/D":
            m2 = re.search(r"nell'esercizio\s+(\d{4})", html, re.IGNORECASE)
            if not m2:
                m2 = re.search(r"(?:fatturato|bilancio|esercizio)[^(]{0,40}\((\d{4})\)", html, re.IGNORECASE)
            if m2:
                result["anno_bilancio"] = m2.group(1)

        # --- Utile/perdita from body if not found yet ---
        if result["utile_perdita"] == "N/D":
            m3 = re.search(r"(?:utile|perdita)[^<]*?<b>\s*([-\d.,]+)\s*€", html, re.IGNORECASE)
            if m3:
                result["utile_perdita"] = "€ " + m3.group(1).strip()

        # --- Dipendenti ---
        m4 = re.search(r"(\d+)\s*addetti", html, re.IGNORECASE)
        if m4:
            result["dipendenti"] = m4.group(1)

        logger.info(f"fatturatoitalia regex: fatturato={result['fatturato']}, anno={result['anno_bilancio']}")

    except Exception as e:
        logger.warning(f"fatturatoitalia extraction failed: {e}")

    return result


def _fetch_with_playwright(url: str, timeout: int = 30000) -> str:
    """
    Fetch HTML usando Playwright (browser headless) per bypassare protezioni anti-bot.
    Usato come fallback quando requests.get() fallisce con HTTP 403.

    Args:
        url: URL da scrapare
        timeout: timeout in millisecondi (default 30s)

    Returns:
        HTML content della pagina, o stringa vuota se fallisce
    """
    try:
        from playwright.sync_api import sync_playwright

        logger.info(f"[playwright] Fetching with browser: {url}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Naviga alla pagina con timeout
            # Usa "load" invece di "networkidle" per siti con JS asincroni continui
            page.goto(url, timeout=timeout, wait_until="load")

            # Aspetta che la pagina sia caricata completamente
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except:
                pass  # Continua comunque se timeout

            # Estrai HTML
            html = page.content()

            browser.close()

            logger.info(f"[playwright] Fetched {len(html)} chars from {url}")
            return html

    except Exception as e:
        logger.warning(f"[playwright] Errore durante fetch: {e}")
        return ""


def _ufficiocamerale_extract(company_name: str, vat: str) -> dict:
    """
    Cerca l'azienda su ufficiocamerale.it via Tavily API, poi scrape la pagina.
    Usa Playwright come fallback se requests.get() fallisce con HTTP 403 (anti-bot).
    Estrae fatturato, anno bilancio e altri dati finanziari.

    Valida il risultato con fuzzy matching (nome azienda) e P.IVA matching.
    """
    import re

    result = {"fatturato": "N/D", "anno_bilancio": "N/D", "diagnostica": "", "confidence": "N/D", "validated": False}

    try:
        # Step 1: Tavily search per trovare la pagina ufficiocamerale
        search_query = f"{company_name} fatturato site:ufficiocamerale.it"
        if vat and vat != "N/A":
            search_query = f"{company_name} {vat} site:ufficiocamerale.it"

        logger.info(f"[ufficiocamerale] Searching via Tavily: {search_query}")
        tavily_results = _tavily_search(search_query, max_results=3)

        # Se Tavily fallisce, prova WebSearchAPI come fallback
        if not tavily_results:
            logger.info(f"[ufficiocamerale] Tavily non ha trovato risultati, trying WebSearchAPI fallback")
            tavily_results = _websearch_api_search(search_query, max_results=3)

        if not tavily_results:
            result["diagnostica"] = "Tavily + WebSearchAPI: nessun risultato trovato"
            return result

        # Filtra URL ufficiocamerale
        # Pattern URL: https://www.ufficiocamerale.it/{id}/{slug}
        uc_page_url = None
        for item in tavily_results:
            url = item.get("url", "")
            if "ufficiocamerale.it" in url and re.search(r'/\d+/', url):
                uc_page_url = url
                logger.info(f"[ufficiocamerale] Usando pagina: {uc_page_url}")
                break

        if not uc_page_url:
            result["diagnostica"] = "Azienda non trovata su ufficiocamerale.it"
            return result

        # Step 2: Scrape pagina ufficiocamerale
        # Prima prova con requests, se 403 usa Playwright
        html = None
        page_resp = requests.get(uc_page_url, timeout=10, headers=_get_browser_headers())

        if page_resp.status_code == 200:
            html = page_resp.text
            logger.info(f"[ufficiocamerale] Fetched with requests (HTTP 200)")
        elif page_resp.status_code == 403:
            # HTTP 403 = anti-bot, usa Playwright come fallback
            logger.info(f"[ufficiocamerale] HTTP 403 detected, trying Playwright fallback")
            html = _fetch_with_playwright(uc_page_url)
            if not html:
                result["diagnostica"] = f"Pagina ufficiocamerale bloccata (HTTP 403), fallback Playwright fallito"
                return result
        else:
            result["diagnostica"] = f"Pagina ufficiocamerale non accessibile (HTTP {page_resp.status_code})"
            return result

        # Step 3: Estrai fatturato da HTML
        # Pattern comuni su ufficiocamerale.it:
        # - <li class="list-group-item">Fatturato: <strong>€&nbsp;5.045.628,00 </strong>(2024)
        # - "Fatturato: € 1.234.567"
        # - "Ricavi: € 1.234.567"

        # Pattern A: Struttura HTML con <strong> e &nbsp; (più comune)
        fatturato_m = re.search(
            r'(?:Fatturato|Ricavi)[:\s]*<strong>\s*€?\s*&nbsp;\s*([\d\.]+,\d{2})\s*</strong>',
            html, re.IGNORECASE | re.DOTALL
        )

        # Pattern B: Label diretto con valore (senza HTML tags)
        if not fatturato_m:
            fatturato_m = re.search(
                r'(?:Fatturato|Ricavi)[:\s]+€?\s*([\d]{1,3}(?:\.[\d]{3})+(?:,\d{2})?)\s*€?',
                html, re.IGNORECASE
            )

        # Pattern C: In tag/div strutturati generici
        if not fatturato_m:
            fatturato_m = re.search(
                r'(?:fatturato|ricavi).*?[>:]\s*€?\s*([\d]{1,3}(?:\.[\d]{3})+(?:,\d{2})?)\s*€?',
                html, re.IGNORECASE
            )

        if fatturato_m:
            result["fatturato"] = "€ " + fatturato_m.group(1).strip()
            result["confidence"] = "high"  # ufficiocamerale.it è fonte ufficiale
            logger.info(f"[ufficiocamerale] Fatturato trovato: {result['fatturato']}")
            result["diagnostica"] = f"Fatturato trovato su ufficiocamerale.it ({uc_page_url})"

            # === VALIDAZIONE NOME + P.IVA ===
            validation_passed = False
            validation_details = []

            # 1. Fuzzy matching nome azienda
            # Cerca nome azienda nel testo (pattern comuni: <h1>, <title>, meta description)
            name_patterns = [
                r'<h1[^>]*>(.*?)</h1>',
                r'<title>(.*?)</title>',
                r'content="[^"]*?([\w\s\.]+(?:srl|spa|snc|sas|s\.r\.l\.|s\.p\.a\.)).*?"',  # Da meta
            ]

            found_name = None
            for pattern in name_patterns:
                name_m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if name_m:
                    found_name = name_m.group(1).strip()
                    # Pulisci HTML tags
                    found_name = re.sub(r'<[^>]+>', '', found_name).strip()
                    if len(found_name) > 5:  # Nome minimo plausibile
                        break

            if found_name and _fuzzy_match_company_name(company_name, found_name):
                validation_passed = True
                validation_details.append(f"nome validato ('{found_name}')")
                logger.info(f"[ufficiocamerale] ✅ Nome validato: '{company_name}' ~ '{found_name}'")

            # 2. P.IVA matching
            if vat and vat != "N/A" and _find_vat_in_html(html, vat):
                validation_passed = True
                validation_details.append("P.IVA validato")
                logger.info(f"[ufficiocamerale] ✅ P.IVA validato: {vat}")

            result["validated"] = validation_passed
            if validation_details:
                result["diagnostica"] += f" ({', '.join(validation_details)})"
            else:
                result["diagnostica"] += " (⚠️ nome/P.IVA non verificato)"
                logger.warning(f"[ufficiocamerale] ⚠️ Fatturato trovato ma nome/P.IVA non validato")

        else:
            result["diagnostica"] = "Pagina ufficiocamerale trovata ma fatturato non estratto"

        # Estrai anno bilancio se disponibile
        # Pattern A: Anno tra parentesi dopo fatturato (es. </strong>(2024))
        anno_m = re.search(r'(?:Fatturato|Ricavi).*?</strong>\s*\((\d{4})\)', html, re.IGNORECASE | re.DOTALL)

        # Pattern B: Label esplicita (es. "Anno: 2024")
        if not anno_m:
            anno_m = re.search(r'(?:Anno|Esercizio|Bilancio)[:\s]+(\d{4})', html, re.IGNORECASE)

        if anno_m:
            result["anno_bilancio"] = anno_m.group(1)

    except Exception as e:
        result["diagnostica"] = f"Errore durante estrazione: {str(e)}"
        logger.warning(f"[ufficiocamerale] Errore: {e}")

    return result


def _registroaziende_extract(company_name: str, vat: str) -> dict:
    """
    Cerca l'azienda su registroaziende.it con accesso diretto (pattern URL) + Tavily fallback.
    Estrae fatturato, dipendenti, utile e altri dati finanziari.

    Valida il risultato con fuzzy matching (nome azienda) e P.IVA matching.
    """
    import re
    import unicodedata

    result = {"fatturato": "N/D", "anno_bilancio": "N/D", "diagnostica": "", "confidence": "N/D", "validated": False}
    ra_page_url = None

    try:
        # Step 1: Tentativo accesso diretto con pattern URL
        if vat and vat != "N/A":
            # Crea slug da ragione sociale + VAT
            # Rimuovi caratteri speciali, lowercase, sostituisci spazi con -
            def make_slug(text):
                # Normalizza unicode (es. à -> a)
                text = unicodedata.normalize('NFKD', text)
                text = text.encode('ascii', 'ignore').decode('ascii')
                # Rimuovi tutto tranne lettere, numeri, spazi
                text = re.sub(r'[^a-z0-9\s-]', '', text.lower())
                # Sostituisci spazi multipli con singolo
                text = re.sub(r'\s+', '-', text.strip())
                return text

            company_slug = make_slug(company_name)
            vat_clean = vat.replace("IT", "").replace(" ", "").strip()

            # Varianti del nome (senza forma giuridica, ecc.)
            company_base = re.sub(r'\b(srl|s\.?r\.?l\.?|spa|s\.?p\.?a\.?|snc|sas)\b', '', company_name, flags=re.IGNORECASE).strip()
            company_base_slug = make_slug(company_base)

            # Pattern comuni su registroaziende.it
            direct_patterns = [
                f"https://registroaziende.it/{company_slug}-{vat_clean}",
                f"https://registroaziende.it/azienda/{company_slug}-{vat_clean}",
                f"https://registroaziende.it/{vat_clean}/{company_slug}",
                f"https://registroaziende.it/{company_base_slug}-{vat_clean}",  # Senza forma giuridica
                f"https://registroaziende.it/{vat_clean}",  # Solo VAT
                f"https://registroaziende.it/ricerca?q={vat}",  # Ricerca standard per VAT (include IT prefix)
            ]

            for pattern_url in direct_patterns:
                logger.info(f"[registroaziende] Tentativo accesso diretto: {pattern_url}")
                try:
                    test_resp = requests.get(pattern_url, timeout=5, headers=_get_browser_headers())
                    if test_resp.status_code == 200 and len(test_resp.text) > 5000:
                        # Verifica che sia la pagina giusta (contiene P.IVA)
                        if vat_clean in test_resp.text or company_name.lower() in test_resp.text.lower():
                            ra_page_url = pattern_url
                            logger.info(f"[registroaziende] ✅ Accesso diretto riuscito: {ra_page_url}")
                            break
                except:
                    continue

        # Step 2: Se accesso diretto fallisce, usa Tavily
        if not ra_page_url:
            search_query = f"{company_name} fatturato site:registroaziende.it"
            if vat and vat != "N/A":
                search_query = f"{company_name} {vat} site:registroaziende.it"

            logger.info(f"[registroaziende] Accesso diretto fallito, usando Tavily: {search_query}")
            tavily_results = _tavily_search(search_query, max_results=5)

            # Se Tavily fallisce, prova WebSearchAPI come fallback
            if not tavily_results:
                logger.info(f"[registroaziende] Tavily non ha trovato risultati, trying WebSearchAPI fallback")
                tavily_results = _websearch_api_search(search_query, max_results=5)

            if not tavily_results:
                result["diagnostica"] = "Tavily + WebSearchAPI: nessun risultato trovato"
                return result

            # Filtra URL registroaziende, escludi pagine generiche
            for item in tavily_results:
                url = item.get("url", "")
                if "registroaziende.it" in url:
                    # Escludi pagine generiche
                    if any(skip in url for skip in ["/ricerca", "/piattaforma", "/b2b"]):
                        continue
                    ra_page_url = url
                    logger.info(f"[registroaziende] Usando pagina da Tavily: {ra_page_url}")
                    break

        if not ra_page_url:
            result["diagnostica"] = "Azienda non trovata su registroaziende.it"
            return result

        # Step 2: Scrape pagina registroaziende
        page_resp = requests.get(ra_page_url, timeout=10, headers=_get_browser_headers())
        if page_resp.status_code != 200:
            result["diagnostica"] = f"Pagina registroaziende non accessibile (HTTP {page_resp.status_code})"
            return result

        html = page_resp.text

        # Step 3: Estrai fatturato da HTML
        # Pattern comuni su registroaziende.it:
        # - "Fatturato: € 1.234.567"
        # - "Ricavi: € 1.234.567"
        # - Tabelle con label/value

        # Pattern A: Label con valore
        fatturato_m = re.search(
            r'(?:Fatturato|Ricavi|Revenue)[:\s]*€?\s*([\d]{1,3}(?:\.[\d]{3})+(?:,\d{2})?)\s*€?',
            html, re.IGNORECASE
        )

        # Pattern B: In strutture HTML (div, td, span)
        if not fatturato_m:
            fatturato_m = re.search(
                r'(?:fatturato|ricavi).*?>.*?€?\s*([\d]{1,3}(?:\.[\d]{3})+(?:,\d{2})?)\s*€?',
                html, re.IGNORECASE
            )

        if fatturato_m:
            result["fatturato"] = "€ " + fatturato_m.group(1).strip()
            result["confidence"] = "high"  # registroaziende.it è fonte ufficiale
            logger.info(f"[registroaziende] Fatturato trovato: {result['fatturato']}")
            result["diagnostica"] = f"Fatturato trovato su registroaziende.it ({ra_page_url})"

            # === VALIDAZIONE NOME + P.IVA ===
            validation_passed = False
            validation_details = []

            # 1. Fuzzy matching nome azienda
            # Cerca nome azienda nel testo (pattern comuni: <h1>, <title>, meta description)
            name_patterns = [
                r'<h1[^>]*>(.*?)</h1>',
                r'<title>(.*?)</title>',
                r'content="[^"]*?([\w\s\.]+(?:srl|spa|snc|sas|s\.r\.l\.|s\.p\.a\.)).*?"',  # Da meta
            ]

            found_name = None
            for pattern in name_patterns:
                name_m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if name_m:
                    found_name = name_m.group(1).strip()
                    # Pulisci HTML tags
                    found_name = re.sub(r'<[^>]+>', '', found_name).strip()
                    if len(found_name) > 5:  # Nome minimo plausibile
                        break

            if found_name and _fuzzy_match_company_name(company_name, found_name):
                validation_passed = True
                validation_details.append(f"nome validato ('{found_name}')")
                logger.info(f"[registroaziende] ✅ Nome validato: '{company_name}' ~ '{found_name}'")

            # 2. P.IVA matching
            if vat and vat != "N/A" and _find_vat_in_html(html, vat):
                validation_passed = True
                validation_details.append("P.IVA validato")
                logger.info(f"[registroaziende] ✅ P.IVA validato: {vat}")

            result["validated"] = validation_passed
            if validation_details:
                result["diagnostica"] += f" ({', '.join(validation_details)})"
            else:
                result["diagnostica"] += " (⚠️ nome/P.IVA non verificato)"
                logger.warning(f"[registroaziende] ⚠️ Fatturato trovato ma nome/P.IVA non validato")

        else:
            result["diagnostica"] = "Pagina registroaziende trovata ma fatturato non estratto"

        # Estrai anno bilancio se disponibile
        anno_m = re.search(r'(?:Anno|Esercizio|Bilancio)[:\s]+(\d{4})', html, re.IGNORECASE)
        if anno_m:
            result["anno_bilancio"] = anno_m.group(1)

    except Exception as e:
        result["diagnostica"] = f"Errore durante estrazione: {str(e)}"
        logger.warning(f"[registroaziende] Errore: {e}")

    return result


def _atoka_extract(company_name: str, vat: str) -> dict:
    """
    Cerca l'azienda su Atoka con accesso diretto (pattern URL) + Tavily fallback.
    Atoka contiene dati strutturati (JSON-LD) con ricavi e fatturato.

    Valida il risultato con fuzzy matching (nome azienda) e P.IVA matching.
    """
    import re
    import unicodedata
    import json as json_module

    result = {"fatturato": "N/D", "ragione_sociale": "N/D", "anno_bilancio": "N/D", "diagnostica": "", "confidence": "N/D", "validated": False}
    atoka_page_url = None
    vat_clean = vat.replace("IT", "").replace(" ", "").strip() if vat and vat != "N/A" else ""

    try:
        # Step 1: Tentativo accesso diretto con pattern URL
        if vat_clean:
            # Crea slug da ragione sociale
            def make_slug(text):
                # Normalizza unicode (es. à -> a)
                text = unicodedata.normalize('NFKD', text)
                text = text.encode('ascii', 'ignore').decode('ascii')
                # Rimuovi tutto tranne lettere, numeri, spazi
                text = re.sub(r'[^a-z0-9\s-]', '', text.lower())
                # Sostituisci spazi multipli con singolo
                text = re.sub(r'\s+', '-', text.strip())
                return text

            company_slug = make_slug(company_name)

            # Varianti del nome (senza forma giuridica, ecc.)
            company_base = re.sub(r'\b(srl|s\.?r\.?l\.?|spa|s\.?p\.?a\.?|snc|sas)\b', '', company_name, flags=re.IGNORECASE).strip()
            company_base_slug = make_slug(company_base)

            # Pattern Atoka: /public/it/azienda/{slug}-{piva}
            direct_patterns = [
                f"https://atoka.io/public/it/azienda/{company_slug}-{vat_clean}",
                f"https://atoka.io/public/it/azienda/{company_base_slug}-{vat_clean}",  # Senza forma giuridica
            ]

            for direct_url in direct_patterns:
                logger.info(f"[atoka] Tentativo accesso diretto: {direct_url}")
                try:
                    test_resp = requests.get(direct_url, timeout=5, headers=_get_browser_headers())
                    if test_resp.status_code == 200 and len(test_resp.text) > 5000:
                        # Verifica che sia la pagina giusta
                        if vat_clean in test_resp.text or company_name.lower() in test_resp.text.lower():
                            atoka_page_url = direct_url
                            logger.info(f"[atoka] ✅ Accesso diretto riuscito: {atoka_page_url}")
                            break
                except:
                    continue

        # Step 2: Se accesso diretto fallisce, usa Tavily
        if not atoka_page_url:
            search_query = f"{company_name} fatturato site:atoka.io"
            if vat and vat != "N/A":
                search_query = f"{company_name} {vat} fatturato site:atoka.io"

            logger.info(f"[atoka] Accesso diretto fallito, usando Tavily: {search_query}")
            tavily_results = _tavily_search(search_query, max_results=5)

            # Se Tavily fallisce, prova WebSearchAPI come fallback
            if not tavily_results:
                logger.info(f"[atoka] Tavily non ha trovato risultati, trying WebSearchAPI fallback")
                tavily_results = _websearch_api_search(search_query, max_results=5)

            if not tavily_results:
                result["diagnostica"] = "Tavily + WebSearchAPI: nessun risultato trovato"
                return result

            # Filtra URL Atoka con VAT corretta
            for item in tavily_results:
                url = item.get("url", "")
                if "atoka.io" in url and "/azienda/" in url:
                    # Se abbiamo VAT, verifica che l'URL la contenga
                    if vat_clean:
                        if vat_clean in url:
                            atoka_page_url = url
                            logger.info(f"[atoka] Usando pagina da Tavily (VAT match): {atoka_page_url}")
                            break
                    else:
                        atoka_page_url = url
                        logger.info(f"[atoka] Usando pagina da Tavily: {atoka_page_url}")
                        break

        if not atoka_page_url:
            result["diagnostica"] = f"Azienda non trovata su Atoka"
            return result

        # Step 2: Scrape pagina Atoka
        page_resp = requests.get(atoka_page_url, timeout=10, headers=_get_browser_headers())
        if page_resp.status_code != 200:
            result["diagnostica"] = f"Pagina Atoka non accessibile (HTTP {page_resp.status_code})"
            return result

        html = page_resp.text

        # Step 3: Estrai fatturato da JSON-LD FAQ
        # Pattern: "I ricavi generati da X sono stati di 23.0 K €"
        # Pattern: "L'ultimo fatturato dichiarato da X ammonta a 23.0 K €"
        ricavi_m = re.search(
            r'ricavi[^"]*?sono stati di\s+([\d.,]+)\s*([KMBkmb])?\s*€',
            html, re.IGNORECASE
        )
        fatturato_m = re.search(
            r'fatturato[^"]*?ammonta a\s+([\d.,]+)\s*([KMBkmb])?\s*€',
            html, re.IGNORECASE
        )

        match = ricavi_m or fatturato_m
        if match:
            value_str = match.group(1).replace(",", ".")
            multiplier_str = (match.group(2) or "").upper()
            try:
                value = float(value_str)
                if multiplier_str == "K":
                    value *= 1_000
                elif multiplier_str == "M":
                    value *= 1_000_000
                elif multiplier_str == "B":
                    value *= 1_000_000_000

                # Formatta il fatturato
                if value >= 1_000_000:
                    result["fatturato"] = f"€ {value/1_000_000:.1f} mln"
                elif value >= 1_000:
                    result["fatturato"] = f"€ {value:,.0f}".replace(",", ".")
                else:
                    result["fatturato"] = f"€ {value:.0f}"
            except ValueError:
                result["fatturato"] = f"€ {match.group(1)} {multiplier_str}".strip()

            result["confidence"] = "high"  # Atoka è fonte ufficiale
            result["diagnostica"] = f"Fatturato trovato su Atoka ({atoka_page_url})"
            logger.info(f"[atoka] Fatturato trovato: {result['fatturato']}")

            # === VALIDAZIONE NOME + P.IVA ===
            validation_passed = False
            validation_details = []

            # 1. Fuzzy matching nome azienda
            # Cerca nome azienda nel testo (pattern comuni: <h1>, <title>, meta description)
            name_patterns = [
                r'<h1[^>]*>(.*?)</h1>',
                r'<title>([^:<]+?)(?:\s*:|\s*\|)',  # Title Atoka format
                r'content="[^"]*?([\w\s\.]+(?:srl|spa|snc|sas|s\.r\.l\.|s\.p\.a\.)).*?"',  # Da meta
            ]

            found_name = None
            for pattern in name_patterns:
                name_m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if name_m:
                    found_name = name_m.group(1).strip()
                    # Pulisci HTML tags
                    found_name = re.sub(r'<[^>]+>', '', found_name).strip()
                    if len(found_name) > 5:  # Nome minimo plausibile
                        break

            if found_name and _fuzzy_match_company_name(company_name, found_name):
                validation_passed = True
                validation_details.append(f"nome validato ('{found_name}')")
                logger.info(f"[atoka] ✅ Nome validato: '{company_name}' ~ '{found_name}'")

            # 2. P.IVA matching
            if vat and vat != "N/A" and _find_vat_in_html(html, vat):
                validation_passed = True
                validation_details.append("P.IVA validato")
                logger.info(f"[atoka] ✅ P.IVA validato: {vat}")

            result["validated"] = validation_passed
            if validation_details:
                result["diagnostica"] += f" ({', '.join(validation_details)})"
            else:
                result["diagnostica"] += " (⚠️ nome/P.IVA non verificato)"
                logger.warning(f"[atoka] ⚠️ Fatturato trovato ma nome/P.IVA non validato")

        else:
            result["diagnostica"] = "Pagina Atoka trovata ma fatturato non estratto"
            logger.info(f"[atoka] Pagina trovata ma nessun dato fatturato")

        # Estrai ragione sociale dal meta/title
        title_m = re.search(r'<title>([^:<]+?)(?:\s*:|\s*\|)', html)
        if title_m:
            result["ragione_sociale"] = title_m.group(1).strip()

    except Exception as e:
        result["diagnostica"] = f"Errore Atoka: {e}"
        logger.warning(f"[atoka] Errore per {company_name}: {e}")

    return result


# _ollama_fallback REMOVED: was unreliable and returned wrong data (wrong companies/VATs)
# Fallback to search pages scraping + LLM extraction was causing more harm than good
# Better to return N/D than wrong data. Keeping _extract_fatturato_from_detail_page for detail pages only.


def _parse_fatturato_to_number(fatturato_str: str) -> float:
    """
    Converte stringa fatturato in numero.
    Esempi: '€ 3.815.456' → 3815456.0, '€ 23.5 mln' → 23500000.0, 'N/D' → 0.0
    """
    import re as re_mod
    if not fatturato_str or fatturato_str.strip() in ("N/D", "N/A", ""):
        return 0.0

    text = fatturato_str.replace("€", "").strip()

    # Formato "23.5 mln" o "1.2 mld"
    mln_match = re_mod.search(r'([\d.,]+)\s*(mln|milion|mld|miliard|[KMBkmb])', text, re.IGNORECASE)
    if mln_match:
        num_str = mln_match.group(1).replace(",", ".")
        multiplier = mln_match.group(2).lower()
        try:
            value = float(num_str)
            if multiplier in ("mln", "milion", "m"):
                value *= 1_000_000
            elif multiplier in ("mld", "miliard", "b"):
                value *= 1_000_000_000
            elif multiplier == "k":
                value *= 1_000
            return value
        except ValueError:
            pass

    # Formato "3.815.456" (punto come separatore migliaia)
    # Rimuovi tutto tranne cifre, punti e virgole
    clean = re_mod.sub(r'[^\d.,]', '', text)
    if not clean:
        return 0.0

    # Se ha virgola come decimale: "3.815.456,00"
    if "," in clean:
        parts = clean.split(",")
        integer_part = parts[0].replace(".", "")
        decimal_part = parts[1] if len(parts) > 1 else "0"
        try:
            return float(f"{integer_part}.{decimal_part}")
        except ValueError:
            return 0.0

    # Solo punti: conta i punti per capire se sono separatori migliaia
    # "3.815.456" (3 punti-gruppi) = migliaia, "3.5" = decimale
    dot_parts = clean.split(".")
    if len(dot_parts) > 2:
        # Multipli punti = separatori migliaia (es. "3.815.456")
        try:
            return float(clean.replace(".", ""))
        except ValueError:
            return 0.0
    elif len(dot_parts) == 2:
        # Un solo punto: se la parte dopo ha 3 cifre, e' migliaia (es. "815.456")
        if len(dot_parts[1]) == 3:
            try:
                return float(clean.replace(".", ""))
            except ValueError:
                return 0.0
        else:
            # Decimale (es. "23.5")
            try:
                return float(clean)
            except ValueError:
                return 0.0
    else:
        try:
            return float(clean)
        except ValueError:
            return 0.0


def search_company_revenue(company_name: str, domain: str = "", vat: str = "",
                          hubspot_online: str = "", hubspot_offline: str = "") -> dict:
    """
    Search for company revenue and legal name with MULTI-SOURCE VALIDATION.

    Strategy (6-step + validation):
    1. VIES API (EU) -> ragione sociale ufficiale dalla P.IVA
    2. fatturatoitalia.it detail page (direct URL) -> fatturato via regex
    3. ufficiocamerale.it (via Tavily API) -> fatturato da HTML scraping
    4. registroaziende.it (accesso diretto + Tavily fallback) -> fatturato da HTML scraping
    5. Atoka (accesso diretto + Tavily fallback) -> fatturato da JSON-LD
    6. Fallback: scrape search pages + gemma3:4b local LLM
    7. Multi-source validation: confronta valori e ajusta confidence

    Returns: {"fatturato": str, "ragione_sociale": str, "source": str, "raw": str, "diagnostics": list, "confidence": str}
    """
    result = {
        "fatturato": "N/D",
        "ragione_sociale": "N/D",
        "source": "",
        "raw": "",
        "diagnostics": [],
        "confidence": "N/D"  # high/medium/low - affidabilità del dato fatturato
    }

    # Lista per raccogliere valori da tutte le fonti
    all_sources = []

    # === STEP 1: VIES lookup for official company name + country code ===
    vies_name = ""
    is_italian_vat = False  # Will be determined by VIES response

    if vat and vat != "N/A":
        logger.info(f"VIES lookup for P.IVA: {vat}")
        vies_data = _vies_lookup(vat)

        # Extract country code from VIES (authoritative source)
        country_code = vies_data.get("country_code", "")
        if country_code:
            is_italian_vat = (country_code == "IT")
            if not is_italian_vat:
                logger.info(f"⚠️ VAT {country_code} (non italiano) - fonti italiane verranno saltate")
                result["diagnostics"].append(f"VAT {country_code}: fonti italiane (fatturatoitalia, ufficiocamerale, registroaziende, atoka) non consultate")

        if vies_data.get("name"):
            vies_name = vies_data["name"]
            result["ragione_sociale"] = vies_name
            result["source"] = "VIES"
            result["diagnostics"].append(f"VIES: P.IVA valida ({country_code}), ragione sociale = {vies_name}")
            logger.info(f"VIES: {vies_name}")
        else:
            result["diagnostics"].append("VIES: P.IVA non valida o non trovata nel registro europeo")
    else:
        result["diagnostics"].append("P.IVA non fornita — VIES non consultato")

    # === STEP 2: fatturatoitalia detail page (regex) ===
    lookup_name = vies_name or company_name
    if is_italian_vat and lookup_name and vat and vat != "N/A":
        fi_data = _fatturatoitalia_extract(lookup_name, vat)
        if fi_data["fatturato"] != "N/D":
            # Raccogli valore per multi-source validation
            all_sources.append({
                "source": "fatturatoitalia.it",
                "value": fi_data["fatturato"],
                "confidence": fi_data.get("confidence", "N/D"),
                "validated": True,  # fatturatoitalia usa URL diretto con P.IVA quindi è sempre validato
                "raw": f"anno: {fi_data['anno_bilancio']}, utile/perdita: {fi_data['utile_perdita']}, dipendenti: {fi_data['dipendenti']}"
            })
            logger.info(f"fatturatoitalia: fatturato={fi_data['fatturato']}, confidence={fi_data.get('confidence')}")

            # Se diagnostica contiene info su scarto, aggiungila
            if fi_data.get("diagnostica"):
                result["diagnostics"].append(f"fatturatoitalia.it: {fi_data['diagnostica']}")
        else:
            # Se c'è una diagnostica specifica (es. "scartato per capitale sociale"), mostrala
            if fi_data.get("diagnostica"):
                result["diagnostics"].append(f"fatturatoitalia.it: {fi_data['diagnostica']}")
            else:
                result["diagnostics"].append("fatturatoitalia.it: azienda non trovata (probabilmente bilancio non ancora depositato)")
    else:
        result["diagnostics"].append("fatturatoitalia.it: ricerca non possibile (P.IVA mancante)")

    # === STEP 2.5: ufficiocamerale.it (via DuckDuckGo search + scrape) ===
    if is_italian_vat:
        uc_data = _ufficiocamerale_extract(company_name, vat)
    else:
        uc_data = {"fatturato": "N/D", "diagnostica": "VAT non italiano - fonte saltata"}

    if uc_data["fatturato"] != "N/D":
        # Raccogli valore per multi-source validation
        all_sources.append({
            "source": "ufficiocamerale.it",
            "value": uc_data["fatturato"],
            "confidence": uc_data.get("confidence", "medium"),  # Usa confidence dalla funzione
            "validated": uc_data.get("validated", False),  # Flag validazione nome/P.IVA
            "raw": f"anno: {uc_data.get('anno_bilancio', 'N/D')}" if uc_data.get('anno_bilancio', 'N/D') != "N/D" else ""
        })
        logger.info(f"ufficiocamerale: fatturato={uc_data['fatturato']}")
    else:
        diag = uc_data.get("diagnostica", "Nessun dato trovato")
        result["diagnostics"].append(f"ufficiocamerale.it: {diag}")

    # === STEP 2.6: registroaziende.it (accesso diretto + Tavily fallback) ===
    if is_italian_vat:
        ra_data = _registroaziende_extract(company_name, vat)
    else:
        ra_data = {"fatturato": "N/D", "diagnostica": "VAT non italiano - fonte saltata"}

    if ra_data["fatturato"] != "N/D":
        # Raccogli valore per multi-source validation
        all_sources.append({
            "source": "registroaziende.it",
            "value": ra_data["fatturato"],
            "confidence": ra_data.get("confidence", "medium"),  # Usa confidence dalla funzione
            "validated": ra_data.get("validated", False),  # Flag validazione nome/P.IVA
            "raw": f"anno: {ra_data.get('anno_bilancio', 'N/D')}" if ra_data.get('anno_bilancio', 'N/D') != "N/D" else ""
        })
        logger.info(f"registroaziende: fatturato={ra_data['fatturato']}")
    else:
        diag = ra_data.get("diagnostica", "Nessun dato trovato")
        result["diagnostics"].append(f"registroaziende.it: {diag}")

    # === STEP 3: Atoka (accesso diretto + Tavily fallback) ===
    if is_italian_vat:
        atoka_data = _atoka_extract(company_name, vat)
    else:
        atoka_data = {"fatturato": "N/D", "diagnostica": "VAT non italiano - fonte saltata"}

    if atoka_data["fatturato"] != "N/D":
        # Raccogli valore per multi-source validation
        all_sources.append({
            "source": "Atoka",
            "value": atoka_data["fatturato"],
            "confidence": atoka_data.get("confidence", "high"),  # Usa confidence dalla funzione
            "validated": atoka_data.get("validated", False),  # Flag validazione nome/P.IVA
            "raw": ""
        })
        if atoka_data.get("ragione_sociale", "N/D") != "N/D" and result["ragione_sociale"] == "N/D":
            result["ragione_sociale"] = atoka_data["ragione_sociale"]
        logger.info(f"Atoka: fatturato={atoka_data['fatturato']}")
    else:
        diag = atoka_data.get("diagnostica", "Nessun dato trovato")
        result["diagnostics"].append(f"Atoka: {diag}")

    # === STEP 4: Ollama fallback REMOVED ===
    # Was unreliable: returned wrong companies/VATs, claimed piva_verificata=true when false
    # Better to return N/D than wrong data. If no sources found, we skip to validation with empty list.

    # === STEP 5: MULTI-SOURCE VALIDATION ===
    if all_sources:
        logger.info(f"Multi-source validation: {len(all_sources)} fonti trovate")
        validation = _validate_multi_source_revenue(all_sources, hubspot_online, hubspot_offline)

        result["fatturato"] = validation["best_value"]
        result["source"] = validation["best_source"]
        result["confidence"] = validation["final_confidence"]

        # Prendi raw dalla fonte migliore
        for s in all_sources:
            if s["value"] == validation["best_value"]:
                result["raw"] = s.get("raw", "")
                break

        # Aggiungi note di validazione alla diagnostica
        for note in validation["validation_notes"]:
            result["diagnostics"].append(note)

        logger.info(f"Final: fatturato={result['fatturato']}, source={result['source']}, confidence={result['confidence']}")
    else:
        # Nessuna fonte ha trovato dati
        result["diagnostics"].append("❌ Nessuna fonte ha trovato dati sul fatturato")
        logger.info("No revenue data found from any source")

    return result


def search_payment_stack(domain: str) -> dict:
    """
    Quick check for payment providers on a website.
    Returns: {"providers": list, "has_bnpl": bool, "bnpl_providers": list}
    """
    result = {"providers": [], "has_bnpl": False, "bnpl_providers": []}

    if not domain or domain == "N/A":
        return result

    # Normalize domain
    if not domain.startswith("http"):
        url = f"https://{domain}"
    else:
        url = domain

    try:
        # Quick fetch of homepage
        response = requests.get(url, timeout=10, headers=_get_browser_headers())
        html = response.text.lower()

        # Payment providers to detect
        payment_keywords = {
            "stripe": "Stripe", "paypal": "PayPal", "nexi": "Nexi",
            "adyen": "Adyen", "checkout.com": "Checkout.com",
            "braintree": "Braintree", "square": "Square",
            "satispay": "Satispay", "apple pay": "Apple Pay",
            "google pay": "Google Pay", "postepay": "PostePay"
        }

        bnpl_keywords = {
            "klarna": "Klarna", "clearpay": "Clearpay",
            "afterpay": "Afterpay", "scalapay": "Scalapay",
            "alma": "Alma", "oney": "Oney", "pagolight": "PagoLight",
            "cofidis": "Cofidis", "soisy": "Soisy"
        }

        for keyword, name in payment_keywords.items():
            if re.search(r'(?<![a-z])' + re.escape(keyword) + r'(?![a-z])', html) and name not in result["providers"]:
                result["providers"].append(name)

        for keyword, name in bnpl_keywords.items():
            if re.search(r'(?<![a-z])' + re.escape(keyword) + r'(?![a-z])', html):
                result["has_bnpl"] = True
                if name not in result["bnpl_providers"]:
                    result["bnpl_providers"].append(name)

    except Exception as e:
        logger.warning(f"Payment stack check failed for {domain}: {e}")

    return result


def enhanced_payment_detection(domain: str) -> dict:
    """
    Enhanced BNPL detection: 3 mandatory steps with agent-browser + HTTP fallback.

    Steps (all 3 always attempted):
    1. Homepage - HTTP fetch
    2. Product page - agent-browser navigation, fallback to URL parsing from HTML
    3. Cart/Checkout - agent-browser add-to-cart flow, ALWAYS backed by direct HTTP
       fetch on common cart/checkout paths (/cart, /carrello, /checkout, /cassa)

    Returns: {
        "providers": list,
        "has_bnpl": bool,
        "bnpl_providers": list,
        "bnpl_locations": {"homepage": bool, "pdp": bool, "checkout": bool},
        "method": "agent-browser" or "http"
    }
    """
    import re
    import time

    # Setup NVM for agent-browser
    NVM_SETUP = 'export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"'

    # Common cart/checkout paths to always check via HTTP
    CART_CHECKOUT_PATHS = [
        "/cart", "/carrello", "/basket", "/shopping-cart",
        "/checkout", "/cassa", "/payment", "/order",
    ]

    result = {
        "providers": [],
        "has_bnpl": False,
        "bnpl_providers": [],
        "bnpl_locations": {"homepage": False, "pdp": False, "checkout": False},
        "confidence": {"score": 0, "label": "low", "reason": ""},
        "method": "http",
        "blocked_by": None
    }

    if not domain or domain == "N/A":
        return result

    # Normalize URL
    base_url = f"https://{domain}" if not domain.startswith("http") else domain
    # Strip trailing slash for consistent path joining
    base_url = base_url.rstrip("/")

    # BNPL keywords to detect
    # NOTE: "alma" RIMOSSA - troppi false positive (es. Louis Vuitton Alma bag, "calma", etc.)
    # Il provider BNPL Alma (Francia) esiste ma la keyword è troppo generica per detection affidabile
    bnpl_keywords = {
        "klarna": "Klarna", "clearpay": "Clearpay",
        "afterpay": "Afterpay", "scalapay": "Scalapay",
        "oney": "Oney", "pagolight": "PagoLight",
        "cofidis": "Cofidis", "soisy": "Soisy", "heylight": "Heylight",
        "pay in 3": "PayPal Pay in 3", "pay in 4": "Pay in 4",
        "paga in 3": "Pay in 3", "paga in 4": "Pay in 4"
    }

    payment_keywords = {
        "stripe": "Stripe", "paypal": "PayPal", "nexi": "Nexi",
        "adyen": "Adyen", "square": "Square", "satispay": "Satispay",
        "apple pay": "Apple Pay", "google pay": "Google Pay"
    }

    checked_urls = set()  # Avoid fetching the same URL twice

    def fetch_and_check_url(check_url: str, location: str) -> str:
        """HTTP fetch a URL, check for payment/BNPL providers. Returns HTML or empty."""
        if check_url in checked_urls:
            return ""
        checked_urls.add(check_url)
        try:
            response = requests.get(check_url, timeout=15, headers=_get_browser_headers(), allow_redirects=True)
            if response.status_code >= 400:
                # Detect Cloudflare / bot protection
                body_lower = response.text.lower()
                is_cloudflare = "cf-ray" in response.headers or "cloudflare" in response.headers.get("server", "").lower() or "cloudflare" in body_lower
                if response.status_code == 403 and is_cloudflare:
                    result["blocked_by"] = "Cloudflare"
                    logger.warning(f"[payment] Blocked by Cloudflare at {check_url[:60]}")
                elif response.status_code in (403, 503) and ("captcha" in body_lower or "challenge" in body_lower or "verifying you are human" in body_lower):
                    result["blocked_by"] = result["blocked_by"] or "Bot protection"
                    logger.warning(f"[payment] Blocked by bot protection at {check_url[:60]}")
                return ""
            html_lower = response.text.lower()

            # Check for BNPL (word boundary to avoid false positives like "money" → "oney")
            for keyword, name in bnpl_keywords.items():
                if re.search(r'(?<![a-z])' + re.escape(keyword) + r'(?![a-z])', html_lower):
                    result["has_bnpl"] = True
                    result["bnpl_locations"][location] = True
                    if name not in result["bnpl_providers"]:
                        result["bnpl_providers"].append(name)

            # Check for payment providers (word boundary)
            for keyword, name in payment_keywords.items():
                if re.search(r'(?<![a-z])' + re.escape(keyword) + r'(?![a-z])', html_lower) and name not in result["providers"]:
                    result["providers"].append(name)

            logger.info(f"[payment] Checked {location} at {check_url[:60]}... - BNPL: {result['bnpl_locations'][location]}")
            return response.text
        except Exception as e:
            logger.warning(f"[payment] Failed to fetch {location} at {check_url}: {e}")
            return ""

    def find_product_urls_from_html(html: str) -> list:
        """Parse homepage HTML to find product page URLs."""
        # Common e-commerce URL patterns
        patterns = [
            r'href="(/products?/[^"#?]+)"',
            r'href="(/shop/[^"#?]+)"',
            r'href="(/p/[^"#?]+)"',
            r'href="(/item/[^"#?]+)"',
            # Italian patterns
            r'href="(/prodott[oi]/[^"#?]+)"',
            # Catch deep category/product pages (3+ segments)
            r'href="(/[a-z0-9-]+/[a-z0-9-]+/[a-z0-9-]+(?:/[a-z0-9-]+)?)"',
        ]
        urls = []
        seen = set()
        for pattern in patterns:
            for match in re.finditer(pattern, html, re.IGNORECASE):
                path = match.group(1)
                # Skip common non-product paths
                if any(skip in path.lower() for skip in [
                    "/login", "/accesso", "/register", "/account", "/cart", "/carrello",
                    "/checkout", "/cassa", "/privacy", "/cookie", "/terms", "/contatt",
                    "/about", "/chi-siamo", "/blog", "/news", "/faq", "/help",
                    "/wishlist", "/lista-desideri", "/image/", "/static/", "/css/", "/js/"
                ]):
                    continue
                if path not in seen:
                    seen.add(path)
                    urls.append(path)
        return urls[:5]  # Return max 5 candidates

    logger.info(f"[payment] Starting 3-step detection for {domain}")

    def check_snapshot_for_bnpl(snapshot_text: str, location: str):
        """Parse agent-browser snapshot (accessibility tree) for BNPL keywords.
        This catches JS-rendered content that HTTP fetch misses."""
        snap_lower = snapshot_text.lower()
        # Detect Cloudflare / bot protection in snapshot
        if "verifying you are human" in snap_lower or ("cloudflare" in snap_lower and "security" in snap_lower):
            result["blocked_by"] = result["blocked_by"] or "Cloudflare"
            logger.warning(f"[payment] Agent-browser blocked by Cloudflare at {location}")
            return
        if "captcha" in snap_lower and len(snapshot_text) < 500:
            result["blocked_by"] = result["blocked_by"] or "Bot protection (captcha)"
            logger.warning(f"[payment] Agent-browser blocked by captcha at {location}")
            return
        for keyword, name in bnpl_keywords.items():
            if re.search(r'(?<![a-z])' + re.escape(keyword) + r'(?![a-z])', snap_lower):
                result["has_bnpl"] = True
                result["bnpl_locations"][location] = True
                if name not in result["bnpl_providers"]:
                    result["bnpl_providers"].append(name)
                    logger.info(f"[payment] BNPL '{name}' found in {location} snapshot")

    def agent_cmd(session: str, action: str, timeout: int = 15) -> str:
        """Run agent-browser command and return stdout."""
        cmd = f'{NVM_SETUP} && agent-browser --session {session} {action}'
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout, text=True)
        return r.stdout.strip()

    # =========================================================
    # STEP 1: HOMEPAGE
    # =========================================================
    homepage_html = fetch_and_check_url(base_url, "homepage")

    # =========================================================
    # STEP 2 + 3: HAIKU-GUIDED BROWSER NAVIGATION
    # Homepage → Product → Add to Cart → Cart → Checkout
    # Haiku analyzes each snapshot and decides what to click.
    # =========================================================
    has_agent_browser = False
    session = f"check_{int(time.time())}"

    try:
        test_cmd = f'{NVM_SETUP} && which agent-browser'
        test_result = subprocess.run(test_cmd, shell=True, capture_output=True, timeout=5)
        has_agent_browser = test_result.returncode == 0
    except Exception:
        pass

    pdp_reached = False
    checkout_reached = False

    def haiku_decide(snapshot: str, task: str) -> str:
        """Ask Haiku to analyze a snapshot and return a ref to click or info.
        Returns the raw Haiku response text."""
        import json as json_mod
        prompt = f"""Sei un agente che naviga un sito e-commerce. Hai davanti l'accessibility tree di una pagina.

TASK: {task}

ACCESSIBILITY TREE:
{snapshot[:4000]}

Rispondi SOLO con un JSON:
{{"ref": "eN", "reasoning": "breve spiegazione"}}

Se non trovi nessun elemento adatto, rispondi:
{{"ref": null, "reasoning": "motivo"}}"""

        escaped = prompt.replace('"', '\\"').replace("'", "'\\''")
        cmd = f'"{CLAUDE_BIN}" --model haiku --print -p "{escaped}"'
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15, cwd=SCRIPT_DIR)
            resp = r.stdout.strip()
            if "```json" in resp:
                resp = resp.split("```json")[1].split("```")[0]
            elif "```" in resp:
                resp = resp.split("```")[1].split("```")[0]
            return json_mod.loads(resp.strip())
        except Exception as e:
            logger.warning(f"[payment] Haiku decide failed: {e}")
            return {"ref": None, "reasoning": str(e)}

    def haiku_find_product_url(html: str):
        """Ask Haiku to identify a product or collection URL from homepage HTML links."""
        # Only match single-slash relative paths (skip // protocol-relative CDN URLs)
        hrefs = re.findall(r'href="(/(?!/)[^"#?][^"]*)"', html, re.IGNORECASE)
        seen = set()
        clean_hrefs = []
        skip_prefixes = ("/cdn", "/static", "/assets", "/js/", "/css/", "/images/", "/img/", "/_")
        skip_extensions = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".woff", ".ttf")
        skip_exact = ("/", "/cart", "/checkout", "/search", "/account", "/login")
        for href in hrefs:
            href_lower = href.lower()
            # Strip query params for filtering
            href_base = href_lower.split("?")[0].split("#")[0]
            if href_base in seen or href_base in skip_exact:
                continue
            seen.add(href_base)
            if any(href_base.startswith(p) for p in skip_prefixes):
                continue
            if any(href_base.endswith(ext) for ext in skip_extensions):
                continue
            if any(c in href for c in ('`', '$', '\\', '"')):
                continue
            clean_hrefs.append(href_base)
        if not clean_hrefs:
            return None
        links_text = "\n".join(clean_hrefs[:60])
        prompt = f"""Sei un assistente che analizza URL di un sito e-commerce.
Da questa lista, scegli il path MIGLIORE per trovare un prodotto acquistabile.

REGOLE:
- Se c'è un path /products/ o /product/ o /p/ → scegli quello
- Se ci sono SOLO path /collections/ → scegli una collezione che sembra contenere prodotti acquistabili
- Rispondi con UNA SOLA RIGA contenente SOLO il path (es: /collections/candele-profumate)

PATHS:
{links_text}"""
        escaped = prompt.replace('"', '\\"').replace("'", "'\\''")
        cmd = f'"{CLAUDE_BIN}" --model haiku --print -p "{escaped}"'
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15, cwd=SCRIPT_DIR)
            resp = r.stdout.strip()
            # Extract a path from the response (Haiku might add explanation)
            for line in resp.split("\n"):
                line = line.strip().strip('`').strip("'").strip('"').strip('*').strip()
                if line.startswith("/") and " " not in line:
                    logger.info(f"[payment] Haiku found URL path: {line}")
                    return line
        except Exception as e:
            logger.warning(f"[payment] Haiku product URL finder failed: {e}")
        return None

    def extract_url_from_snapshot_ref(snapshot: str, html: str, ref: str):
        """Given a ref from agent-browser snapshot, find the URL by matching link text to HTML."""
        match = re.search(r'- link "([^"]+)" \[ref=' + re.escape(ref) + r'\]', snapshot)
        if not match:
            return None
        link_text = match.group(1).strip()
        if not link_text or len(link_text) < 2:
            return None
        escaped_text = re.escape(link_text)
        short_text = re.escape(link_text[:20])
        # Pattern 1: text directly after <a> tag
        m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>\s*' + escaped_text, html, re.IGNORECASE)
        if m:
            return m.group(1)
        # Pattern 2: partial text match after tag
        m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>[^<]*' + short_text, html, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
        # Pattern 3: text in title/aria-label attribute
        m = re.search(r'<a[^>]+href="([^"]+)"[^>]+(?:title|aria-label)="[^"]*' + short_text, html, re.IGNORECASE)
        if m:
            return m.group(1)
        # Pattern 4: text inside nested elements within <a> tag
        m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>[\s\S]{0,200}' + short_text, html, re.IGNORECASE)
        if m:
            return m.group(1)
        return None

    # =========================================================
    # STEP 2: PRODUCT PAGE DISCOVERY (multi-layered)
    # 2a: Regex patterns on HTML (fast, no LLM)
    # 2b: Haiku analyzes HTML links (fallback)
    # 2c: Collection drill-down if Haiku finds collection
    # =========================================================
    pdp_url = None
    pdp_html = None

    if homepage_html:
        # 2a: Regex-based URL finder (fast, no LLM call)
        product_urls = find_product_urls_from_html(homepage_html)
        for path in product_urls:
            product_url = base_url + path if path.startswith("/") else path
            html = fetch_and_check_url(product_url, "pdp")
            if html:
                pdp_reached = True
                pdp_url = product_url
                pdp_html = html
                logger.info(f"[payment] Product found via regex: {pdp_url}")
                break

        # 2b: Haiku analyzes HTML links to find product URL (fallback)
        if not pdp_reached:
            haiku_path = haiku_find_product_url(homepage_html)
            if haiku_path:
                haiku_url = base_url + haiku_path
                html = fetch_and_check_url(haiku_url, "pdp")
                if html:
                    is_product = any(p in haiku_path.lower() for p in ["/product", "/p/", "/item/", "/prodott"])
                    if is_product:
                        pdp_reached = True
                        pdp_url = haiku_url
                        pdp_html = html
                        logger.info(f"[payment] Product found via Haiku: {pdp_url}")
                    else:
                        # 2c: Collection page - drill down for actual products
                        logger.info(f"[payment] Haiku found collection: {haiku_url}, drilling down...")
                        collection_products = find_product_urls_from_html(html)
                        if not collection_products:
                            haiku_path2 = haiku_find_product_url(html)
                            if haiku_path2:
                                collection_products = [haiku_path2]
                        for path in collection_products[:3]:
                            product_url = base_url + path if path.startswith("/") else path
                            html2 = fetch_and_check_url(product_url, "pdp")
                            if html2:
                                pdp_reached = True
                                pdp_url = product_url
                                pdp_html = html2
                                logger.info(f"[payment] Product found via collection drill-down: {pdp_url}")
                                break

    # =========================================================
    # STEP 3: AGENT-BROWSER CHECKOUT FLOW
    # Haiku-guided: snapshot → Haiku decides → navigate/click
    # =========================================================
    if has_agent_browser:
        result["method"] = "agent-browser"
        try:
            # 3a: Last resort - snapshot-based product discovery (JS-rendered sites)
            if not pdp_reached and homepage_html:
                agent_cmd(session, f'open "{base_url}"', timeout=30)
                time.sleep(2)
                hp_snap = agent_cmd(session, 'snapshot -i')
                check_snapshot_for_bnpl(hp_snap, "homepage")

                decision = haiku_decide(hp_snap, "Trova un link che porta a un PRODOTTO singolo acquistabile. Se non c'è, trova una COLLEZIONE o CATEGORIA di prodotti.")
                if decision.get("ref"):
                    ref = decision["ref"]
                    logger.info(f"[payment] Haiku found link in snapshot: @{ref} - {decision.get('reasoning', '')}")
                    href = extract_url_from_snapshot_ref(hp_snap, homepage_html, ref)
                    if href:
                        product_url = (base_url + href) if href.startswith("/") else href
                        html = fetch_and_check_url(product_url, "pdp")
                        if html:
                            pdp_reached = True
                            pdp_url = product_url
                            pdp_html = html
                            logger.info(f"[payment] Product found via snapshot: {pdp_url}")
                            # If it's a collection, drill down
                            if not any(p in product_url.lower() for p in ["/product", "/p/", "/item/", "/prodott"]):
                                inner_products = find_product_urls_from_html(html)
                                for ipath in inner_products[:3]:
                                    inner_url = base_url + ipath if ipath.startswith("/") else ipath
                                    html2 = fetch_and_check_url(inner_url, "pdp")
                                    if html2:
                                        pdp_url = inner_url
                                        pdp_html = html2
                                        break
                    else:
                        # Fallback: click the ref directly
                        try:
                            agent_cmd(session, f'click @{ref}', timeout=15)
                            time.sleep(2)
                            new_url = agent_cmd(session, 'get url', timeout=10)
                            if new_url and new_url != base_url and new_url != base_url + "/":
                                html = fetch_and_check_url(new_url, "pdp")
                                if html:
                                    pdp_reached = True
                                    pdp_url = new_url
                                    pdp_html = html
                        except Exception:
                            logger.warning(f"[payment] Snapshot click fallback failed")

            # 3b: Open product page (or homepage if nothing found)
            open_url = pdp_url or base_url
            agent_cmd(session, f'open "{open_url}"', timeout=30)
            time.sleep(2)
            snap = agent_cmd(session, 'snapshot -i')
            check_snapshot_for_bnpl(snap, "pdp" if pdp_reached else "homepage")

            # 3c: Add to cart via JS eval (bypasses Playwright click timeout)
            if pdp_reached:
                add_js = '(function(){var b=document.querySelectorAll("button,[role=button],input[type=submit]");var k=["aggiungi al carrello","add to cart","acquista ora","buy now","buy it now","compra ora"];for(var i=0;i<b.length;i++){var t=b[i].textContent.toLowerCase().trim();for(var j=0;j<k.length;j++){if(t.indexOf(k[j])>=0){b[i].click();return"clicked:"+t.substring(0,40)}}}var s=document.querySelector("[name=add],.product-form__submit,[data-add-to-cart]");if(s){s.click();return"shopify"}return"none"})()'
                try:
                    atc_result = agent_cmd(session, f"eval '{add_js}'", timeout=10)
                    logger.info(f"[payment] Add to cart JS eval: {atc_result}")
                    time.sleep(2)
                except Exception as e:
                    logger.warning(f"[payment] Add to cart JS eval failed: {e}")

            # 3d: Open /cart directly
            agent_cmd(session, f'open "{base_url}/cart"', timeout=20)
            time.sleep(2)
            cart_snap = agent_cmd(session, 'snapshot -i')
            check_snapshot_for_bnpl(cart_snap, "checkout")
            logger.info(f"[payment] Cart snapshot ({len(cart_snap)} chars)")

            # 3e: Checkout via JS eval (bypasses Playwright click timeout)
            checkout_js = '(function(){var b=document.querySelectorAll("button,a,[role=button],input[type=submit]");var k=["checkout","pagamento","procedi al checkout","vai al pagamento","paga ora","cassa","procedi all"];for(var i=0;i<b.length;i++){var t=b[i].textContent.toLowerCase().trim();for(var j=0;j<k.length;j++){if(t.indexOf(k[j])>=0){b[i].click();return"clicked:"+t.substring(0,40)}}}var s=document.querySelector("[name=checkout],.cart__checkout-button,a[href*=checkout]");if(s){s.click();return"shopify"}return"none"})()'
            try:
                ck_result = agent_cmd(session, f"eval '{checkout_js}'", timeout=10)
                logger.info(f"[payment] Checkout JS eval: {ck_result}")
                time.sleep(5)  # Wait for checkout page to load

                # 3f: Snapshot checkout page (JS-rendered BNPL like Klarna)
                checkout_snap = agent_cmd(session, 'snapshot -i')
                check_snapshot_for_bnpl(checkout_snap, "checkout")
                checkout_reached = True
                logger.info(f"[payment] Checkout snapshot ({len(checkout_snap)} chars)")

                # Also HTTP fetch checkout URL
                checkout_url = agent_cmd(session, 'get url', timeout=10)
                if checkout_url:
                    fetch_and_check_url(checkout_url, "checkout")
            except Exception as e:
                logger.warning(f"[payment] Checkout JS eval failed: {e}")

        except Exception as e:
            logger.warning(f"[payment] Agent-browser flow failed: {e}")

    # Safety net: direct HTTP fetch on common cart/checkout paths
    if not result["bnpl_locations"]["checkout"]:
        for path in CART_CHECKOUT_PATHS:
            fetch_and_check_url(f"{base_url}{path}", "checkout")
            if result["bnpl_locations"]["checkout"]:
                break

    # Close agent-browser session
    if has_agent_browser:
        try:
            agent_cmd(session, 'close', timeout=5)
        except Exception:
            pass

    # =========================================================
    # CONFIDENCE SCORE
    # =========================================================
    locs = result["bnpl_locations"]
    steps_checked = sum([
        bool(checked_urls),                    # homepage checked
        pdp_reached,                           # product page checked
        checkout_reached or locs["checkout"] or any(  # checkout checked
            f"{base_url}{p}" in checked_urls for p in CART_CHECKOUT_PATHS
        )
    ])

    if result["has_bnpl"]:
        # BNPL found - confidence based on WHERE
        loc_count = sum([locs["homepage"], locs["pdp"], locs["checkout"]])
        if locs["checkout"]:
            score = 90 if loc_count > 1 else 80
            reason = "BNPL confermato al checkout"
        elif locs["pdp"]:
            score = 65
            reason = "BNPL trovato su pagina prodotto, non verificato al checkout"
        else:
            score = 40
            reason = "BNPL trovato solo in homepage (potrebbe essere solo menzione/logo)"
        # Bonus for agent-browser (navigated real site)
        if result["method"] == "agent-browser":
            score = min(score + 5, 100)
    else:
        # NO BNPL found - confidence based on how many steps we checked
        if result["blocked_by"]:
            score = 20
            reason = f"Sito bloccato da {result['blocked_by']} - analisi non affidabile"
        elif steps_checked == 3:
            score = 85
            reason = "Nessun BNPL rilevato (homepage + prodotto + checkout verificati)"
        elif steps_checked == 2:
            score = 55
            reason = "Nessun BNPL rilevato (2/3 step verificati)"
        else:
            score = 30
            reason = "Nessun BNPL rilevato (solo homepage verificata)"

    if score >= 75:
        label = "high"
    elif score >= 50:
        label = "medium"
    else:
        label = "low"

    result["confidence"] = {"score": score, "label": label, "reason": reason}

    logger.info(f"[payment] Detection complete for {domain}. Providers: {result['providers']}, BNPL: {result['bnpl_providers']}, Locations: {result['bnpl_locations']}, Confidence: {score}/100 ({label}), Method: {result['method']}")
    return result


def send_to_slack(message: str, deal_name: str = "") -> bool:
    """Send a message to Slack channel."""
    if not SLACK_BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN not set - skipping Slack notification")
        return False

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

    # Truncate message if too long (Slack limit is ~40k chars)
    if len(message) > 35000:
        message = message[:35000] + "\n\n... (truncated)"

    payload = {
        "channel": SLACK_CHANNEL,
        "text": f"🎯 *Deal Qualification Report*{f' - {deal_name}' if deal_name else ''}\n\n{message}",
        "mrkdwn": True
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        result = response.json()
        if result.get("ok"):
            logger.info(f"✅ Slack message sent to {SLACK_CHANNEL}")
            return True
        else:
            logger.error(f"Slack API error: {result.get('error')}")
            return False
    except Exception as e:
        logger.error(f"Failed to send Slack message: {e}")
        return False


def check_deal_matches_filters(deal_id: str) -> bool:
    """Check if a deal matches our filters (pipeline + generic_source + not already processed)."""
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/{deal_id}"
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}
    params = {"properties": "pipeline,generic_source,dealname,sql_qualifier_status"}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        props = response.json().get("properties", {})

        deal_name = props.get("dealname", "Unknown")
        pipeline_id = props.get("pipeline", "")
        generic_source = props.get("generic_source", "")
        qualifier_status = props.get("sql_qualifier_status", "")

        # Check if already processed or in progress (prevents duplicates)
        if qualifier_status in ("done", "in_progress"):
            logger.info(f"⏭️ Deal '{deal_name}' already {qualifier_status} - skipping duplicate webhook")
            return False

        # Check filters
        pipeline_match = pipeline_id == TARGET_PIPELINE_ID
        source_match = generic_source == TARGET_GENERIC_SOURCE

        logger.info(f"Deal '{deal_name}' - Pipeline: {pipeline_match}, Source: {source_match}, Status: {qualifier_status or 'new'}")

        if pipeline_match and source_match:
            logger.info(f"✅ Deal '{deal_name}' matches filters!")
            return True
        else:
            logger.info(f"❌ Deal '{deal_name}' skipped (filters not matched)")
            return False

    except Exception as e:
        logger.error(f"Failed to check deal {deal_id}: {e}")
        return False


def verify_hubspot_signature(request_body: bytes, signature: str) -> bool:
    """Verify HubSpot webhook signature (v3)."""
    if not HUBSPOT_CLIENT_SECRET:
        logger.warning("HUBSPOT_CLIENT_SECRET not set - skipping signature verification")
        return True

    # HubSpot v3 signature: SHA-256 HMAC of requestMethod + requestUri + requestBody + timestamp
    # For simplicity, we'll do basic verification
    expected = hmac.new(
        HUBSPOT_CLIENT_SECRET.encode(),
        request_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


# Use nvm-installed claude (stable across updates, unlike Cursor extension path)
CLAUDE_BIN = "/Users/stefano.conforti@scalapay.com/.nvm/versions/node/v24.11.1/bin/claude"


def triage_with_haiku(deal_name: str, domain: str, semrush_data: str, similarweb_data: str,
                      revenue_data: dict = None, payment_data: dict = None,
                      category: str = "N/A", store_type: str = "N/A",
                      wappalyzer_data: str = "") -> dict:
    """
    Quick triage with Haiku (via CLI) to score and categorize deals.
    Uses Claude CLI with --model haiku (no API key needed, uses Max subscription).
    Returns: {"summary": str, "score": int, "reason": str, ...}
    """
    import json as json_module
    from datetime import datetime

    revenue_data = revenue_data or {"fatturato": "N/D", "source": "", "raw": ""}
    payment_data = payment_data or {"providers": [], "has_bnpl": False, "bnpl_providers": [], "bnpl_locations": {}, "method": "http"}
    ollama_was_offline = revenue_data.get("ollama_offline", False)

    # Default if triage fails
    default_result = {
        "summary": "", "score": 0, "reason": "triage_failed",
        "fatturato": revenue_data["fatturato"],
        "ragione_sociale": revenue_data.get("ragione_sociale", "N/D"),
        "revenue_raw": revenue_data.get("raw", ""),
        "revenue_diagnostics": revenue_data.get("diagnostics", []),
        "revenue_confidence": revenue_data.get("confidence", "N/D"),  # Confidence del fatturato
        "aov_estimated": "N/D",
        "payment_providers": payment_data["providers"],
        "bnpl_providers": payment_data["bnpl_providers"],
        "bnpl_locations": payment_data.get("bnpl_locations", {}),
        "payment_method": payment_data.get("method", "http"),
        "payment_confidence": payment_data.get("confidence", {})
    }

    # Format payment info for prompt
    payment_info = ""
    if payment_data["providers"]:
        payment_info += f"Payment providers rilevati: {', '.join(payment_data['providers'])}\n"
    if payment_data["bnpl_providers"]:
        payment_info += f"BNPL competitors rilevati: {', '.join(payment_data['bnpl_providers'])}\n"

    # Build triage prompt - split by store_type (Physical Store vs E-commerce)
    cat_str = category if category and category != "N/A" else "N/D"
    st_str = store_type if store_type and store_type != "N/A" else "N/D"
    is_physical = store_type and store_type != "N/A" and "physical" in store_type.lower()

    # Dati comuni per entrambi i prompt
    deal_context = f"""DEAL: {deal_name}
DOMINIO: {domain if domain != "N/A" else "(non fornito)"}
CATEGORY (da HubSpot): {cat_str}
STORE TYPE (da HubSpot): {st_str}
FATTURATO: {revenue_data["fatturato"]}
{payment_info}
{semrush_data if semrush_data else "Dati SEMrush: non disponibili"}
{similarweb_data if similarweb_data else "Dati SimilarWeb: non disponibili"}
{wappalyzer_data if wappalyzer_data else "Dati Wappalyzer: non disponibili"}"""

    if is_physical:
        # === PHYSICAL STORE: score deterministico basato solo su fatturato ===
        fatturato_num = _parse_fatturato_to_number(revenue_data["fatturato"])
        if fatturato_num <= 0:
            physical_score = 2
        elif fatturato_num < 500_000:
            physical_score = 3
        elif fatturato_num < 1_000_000:
            physical_score = 5
        elif fatturato_num <= 5_000_000:
            physical_score = 6
        else:
            # Ogni €1M sopra €5M = +1 punto, max 10
            extra = int((fatturato_num - 5_000_000) / 1_000_000)
            physical_score = min(6 + extra, 10)

        logger.info(f"Physical store score: {physical_score} (fatturato={fatturato_num:.0f})")

        prompt = f"""Sei un analista BNPL. Questo e' un NEGOZIO FISICO (Physical Store).

{deal_context}

SCORING PHYSICAL STORE (basato SOLO su fatturato):
- Fatturato N/D → Score 2
- < €500K → Score 3
- €500K-€1M → Score 5
- €1M-€5M → Score 6
- Ogni €1M sopra €5M → +1 punto (max 10)

IL SCORE E' GIA' CALCOLATO: {physical_score}. DEVI usare esattamente questo score.

Rispondi SOLO con questo JSON (nessun altro testo):
{{
  "score": {physical_score},
  "is_ecommerce": false,
  "monthly_visits": <numero visite mensili dai dati, 0 se N/D>,
  "has_bnpl_competitor": <true/false se vedi Klarna/Clearpay/Afterpay nei dati>,
  "category": "<settore: Fashion/Electronics/Home/Beauty/Food/Travel/Services/Other>",
  "aov_estimated": "<AOV stimato in euro o 'N/D'>",
  "summary": "<2-3 frasi: tipo di business fisico, fatturato, potenziale in-store BNPL>"
}}"""
    else:
        # === E-COMMERCE: 4 criteri (fatturato, payment, AOV, tech stack) ===
        prompt = f"""Sei un analista BNPL. Analizza questo deal E-COMMERCE basandoti SOLO sui dati forniti.

{deal_context}

CRITERI SCORING E-COMMERCE (per dare score 7-10 TUTTI i 4 criteri devono essere soddisfatti):
1. Fatturato > €1M (OBBLIGATORIO - se N/D o < €1M → score MAX 6)
2. Payment stack moderno (Stripe/PayPal/Adyen/Nexi rilevato)
3. AOV medio-alto €120+ (stimabile da categoria/brand)
4. Tech stack moderno (Shopify o WooCommerce rilevato nei dati Wappalyzer)

NOTA: La categoria merceologica NON e' un criterio di scoring.

Rispondi SOLO con questo JSON (nessun altro testo):
{{
  "score": <1-10 DEVE rispettare i criteri sopra>,
  "is_ecommerce": true,
  "monthly_visits": <numero visite mensili dai dati, 0 se N/D>,
  "has_bnpl_competitor": <true/false se vedi Klarna/Clearpay/Afterpay nei dati>,
  "category": "<settore: Fashion/Electronics/Home/Beauty/Food/Travel/Services/Other>",
  "aov_estimated": "<AOV stimato in euro, es. '€150' o '€50-200' o 'N/D'>",
  "summary": "<2-3 frasi: business, fatturato, fit Scalapay>"
}}"""

    try:
        # Use Claude CLI with --model haiku for fast, cheap triage
        escaped_prompt = prompt.replace('"', '\\"').replace("'", "'\\''")
        cmd = f'"{CLAUDE_BIN}" --model haiku --print -p "{escaped_prompt}"'

        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,  # Fast triage, no tool calls
            cwd=SCRIPT_DIR
        )

        response_text = result.stdout.strip()

        # Log Haiku usage (estimate tokens)
        usage_log = os.path.join(SCRIPT_DIR, "usage.log")
        estimated_tokens = len(prompt) // 4 + len(response_text) // 4
        with open(usage_log, "a") as f:
            f.write(f"{datetime.now().isoformat()}|{deal_name}|HAIKU|{estimated_tokens}|{len(prompt)}\n")

        logger.info(f"Haiku triage response: {response_text[:200]}...")

        # Parse JSON response
        # Handle markdown code blocks if present
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]

        triage_data = json_module.loads(response_text.strip())

        score = int(triage_data.get("score", 0))
        is_ecommerce = triage_data.get("is_ecommerce", False)
        monthly_visits = int(triage_data.get("monthly_visits", 0))
        has_bnpl_competitor = triage_data.get("has_bnpl_competitor", False)
        summary = triage_data.get("summary", "")
        category = triage_data.get("category", "N/D")
        aov_estimated = triage_data.get("aov_estimated", "N/D")

        reason = f"score_{score}"
        logger.info(f"Triage result: score={score}, reason={reason}")

        return {
            "summary": summary,
            "score": score,
            "reason": reason,
            "is_ecommerce": is_ecommerce,
            "monthly_visits": monthly_visits,
            "has_bnpl_competitor": has_bnpl_competitor or payment_data["has_bnpl"],
            "category": category,
            "aov_estimated": aov_estimated,
            "fatturato": revenue_data["fatturato"],
            "ragione_sociale": revenue_data.get("ragione_sociale", "N/D"),
            "revenue_raw": revenue_data.get("raw", ""),
            "revenue_diagnostics": revenue_data.get("diagnostics", []),
            "revenue_confidence": revenue_data.get("confidence", "N/D"),  # Confidence del fatturato
            "ollama_offline": ollama_was_offline,
            "payment_providers": payment_data["providers"],
            "bnpl_providers": payment_data["bnpl_providers"],
            "bnpl_locations": payment_data.get("bnpl_locations", {}),
            "payment_method": payment_data.get("method", "http"),
            "payment_confidence": payment_data.get("confidence", {})
        }

    except json_module.JSONDecodeError as e:
        logger.error(f"Failed to parse Haiku JSON response: {e}")
        logger.error(f"Raw response was: {response_text[:500] if 'response_text' in dir() else 'N/A'}")
        return default_result
    except subprocess.TimeoutExpired:
        logger.error(f"Haiku triage timed out for {deal_name}")
        return default_result
    except Exception as e:
        logger.error(f"Haiku triage error: {e}")
        return default_result


def get_haiku_usage_stats(deal_name: str) -> dict:
    """Calculate usage statistics for Haiku triage (only model used)."""
    from datetime import datetime, timedelta

    # Haiku pricing
    HAIKU_INPUT_PRICE_PER_1M = 0.25   # $0.25 per 1M input tokens
    HAIKU_OUTPUT_PRICE_PER_1M = 1.25  # $1.25 per 1M output tokens
    USD_TO_EUR = 0.92

    stats = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "cost_eur": 0.0,
        "today_cost_usd": 0.0,
        "today_cost_eur": 0.0,
        "today_deals": 0
    }

    try:
        usage_log = os.path.join(SCRIPT_DIR, "usage.log")
        with open(usage_log, "r") as f:
            lines = f.readlines()

        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        today_tokens = 0
        today_deals_set = set()

        for line in lines:
            parts = line.strip().split("|")
            if len(parts) >= 4:
                try:
                    timestamp = datetime.fromisoformat(parts[0])
                    tokens = int(parts[3])

                    # Count today's totals
                    if timestamp >= today_start:
                        today_tokens += tokens
                        today_deals_set.add(parts[1])

                    # Count this deal's tokens
                    if deal_name in parts[1]:
                        stats["input_tokens"] += tokens
                except:
                    continue

        # Estimate output tokens (Haiku response ~50 tokens)
        stats["output_tokens"] = 50
        stats["total_tokens"] = stats["input_tokens"] + stats["output_tokens"]

        # Cost for this deal
        input_cost = (stats["input_tokens"] / 1_000_000) * HAIKU_INPUT_PRICE_PER_1M
        output_cost = (stats["output_tokens"] / 1_000_000) * HAIKU_OUTPUT_PRICE_PER_1M
        stats["cost_usd"] = input_cost + output_cost
        stats["cost_eur"] = stats["cost_usd"] * USD_TO_EUR

        # Cost for today (all deals)
        today_input_cost = (today_tokens / 1_000_000) * HAIKU_INPUT_PRICE_PER_1M
        stats["today_cost_usd"] = today_input_cost
        stats["today_cost_eur"] = today_input_cost * USD_TO_EUR
        stats["today_deals"] = len(today_deals_set)

    except Exception as e:
        logger.error(f"Failed to calculate Haiku usage stats: {e}")

    return stats


def send_haiku_report_to_slack(triage: dict, deal_name: str, deal_id: str, domain: str,
                               product_request: str = "N/A", vat: str = "N/A",
                               category_hs: str = "N/A", store_type: str = "N/A",
                               semrush_data: str = "", similarweb_data: str = "",
                               wappalyzer_data: str = "",
                               online_annual_revenue: str = "",
                               offline_annual_revenue: str = "") -> bool:
    """Send a structured Haiku report to Slack with qualification buttons."""
    if not SLACK_BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN not set - skipping Slack notification")
        return False

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

    # === Extract triage data ===
    score = triage.get("score", 0)
    stars = ":star:" * score + ":white_circle:" * (10 - score)
    summary = triage.get("summary", "N/A")
    is_ecommerce = ":white_check_mark:" if triage.get("is_ecommerce") else ":x:"

    # Deal info from HubSpot
    ragione_sociale = triage.get("ragione_sociale", "N/D")
    category_display = category_hs if category_hs and category_hs != "N/A" else "N/D"
    store_type_display = store_type if store_type and store_type != "N/A" else "N/D"
    online_rev_display = online_annual_revenue if online_annual_revenue else "N/D"
    offline_rev_display = offline_annual_revenue if offline_annual_revenue else "N/D"

    # AOV estimated
    aov_estimated = triage.get("aov_estimated", "N/D")

    # Revenue
    fatturato = triage.get("fatturato", "N/D")
    revenue_confidence = triage.get("revenue_confidence", "N/D")

    # Mostra sempre confidence (anche quando fatturato è N/D)
    if fatturato != "N/D":
        # Quando abbiamo un valore, aggiungi emoji + descrizione
        if revenue_confidence == "high":
            fatturato = f"{fatturato} ✅ _Confidence: HIGH_"
        elif revenue_confidence == "medium":
            fatturato = f"{fatturato} ⚠️ _Confidence: MEDIUM - verificare manualmente_"
        elif revenue_confidence == "low":
            fatturato = f"{fatturato} ❌ _Confidence: LOW - dato probabilmente errato_"
        else:
            fatturato = f"{fatturato} (Confidence: {revenue_confidence})"
    else:
        # Quando N/D, mostra solo confidence senza emoji
        if triage.get("ollama_offline"):
            fatturato = f"N/D :warning: _Ollama offline_ | Confidence: {revenue_confidence}"
        else:
            fatturato = f"N/D | Confidence: {revenue_confidence}"

    revenue_raw = triage.get("revenue_raw", "")
    revenue_diagnostics = triage.get("revenue_diagnostics", [])
    # Extract anno from raw string (format: "anno: 2023, utile/perdita: ...")
    anno = "N/D"
    if revenue_raw:
        for part in revenue_raw.split(","):
            if "anno" in part.lower():
                anno = part.split(":")[-1].strip()
                break

    # Payment detection
    payment_providers = triage.get("payment_providers", [])
    bnpl_providers = triage.get("bnpl_providers", [])
    bnpl_locations = triage.get("bnpl_locations", {})
    payment_method = triage.get("payment_method", "http")
    confidence = triage.get("payment_confidence", {})

    payments_str = ", ".join(payment_providers) if payment_providers else "N/D"
    bnpl_str = ", ".join(bnpl_providers) if bnpl_providers else "-"

    # BNPL location string (HP=homepage, PDP=product, CO=checkout)
    bnpl_loc_str = ""
    if bnpl_locations:
        locs = []
        if bnpl_locations.get("homepage"): locs.append("HP")
        if bnpl_locations.get("pdp"): locs.append("PDP")
        if bnpl_locations.get("checkout"): locs.append("CO")
        if locs:
            bnpl_loc_str = f" [Found in: {'/'.join(locs)}]"

    # Get usage stats
    usage_stats = get_haiku_usage_stats(deal_name)

    # === BUILD SLACK BLOCKS ===
    sections = [
        # --- HEADER ---
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"⚡ Deal Analysis - {deal_name}",
                "emoji": True
            }
        },
        # --- DEAL INFO ---
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":office: *Deal Info*\n"
                    f"• *Ragione Sociale:* {ragione_sociale}\n"
                    f"• *P.IVA:* {vat}\n"
                    f"• *Deal ID:* {deal_id}\n"
                    f"• *Category:* {category_display}\n"
                    f"• *Store Type:* {store_type_display}\n"
                    f"• *Revenue Online (HubSpot):* {online_rev_display}\n"
                    f"• *Revenue Offline (HubSpot):* {offline_rev_display}"
                )
            }
        },
        # --- HAIKU TRIAGE ---
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":brain: *Haiku Triage*\n"
                    f"Score: {stars} ({score}/10)\n"
                    f"E-commerce: {is_ecommerce}\n"
                    f"{summary}"
                )
            }
        },
        {"type": "divider"},
        # --- REVENUE ---
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":moneybag: *Revenue*\n"
                    f"• *Fatturato:* {fatturato}\n"
                    f"• *Anno:* {anno}\n"
                    f"• *AOV Stimato:* {aov_estimated}"
                    + (("\n:mag: *Diagnostica ricerca:*\n" + "\n".join(f"  → _{d}_" for d in revenue_diagnostics)) if revenue_diagnostics else "")
                )
            }
        },
        {"type": "divider"},
    ]

    # --- TRAFFIC: SEMRUSH ---
    if semrush_data:
        sections.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": semrush_data
            }
        })

    # --- TRAFFIC: SIMILARWEB ---
    if similarweb_data:
        sections.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": similarweb_data
            }
        })

    if semrush_data or similarweb_data:
        sections.append({"type": "divider"})

    # --- WAPPALYZER TECHNOLOGIES ---
    if wappalyzer_data:
        sections.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": wappalyzer_data
            }
        })
        sections.append({"type": "divider"})

    # --- PAYMENT DETECTION ---
    confidence_str = ""
    if confidence:
        conf_score = confidence.get("score", 0)
        conf_label = confidence.get("label", "N/D")
        conf_reason = confidence.get("reason", "")
        confidence_str = f"\n• *Confidence:* {conf_score}/100 ({conf_label}) - _{conf_reason}_"

    has_bnpl_icon = ":warning: Si" if triage.get("has_bnpl_competitor") else ":white_check_mark: No"
    bnpl_detail = f" ({bnpl_str}){bnpl_loc_str}" if bnpl_providers else ""

    sections.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f":credit_card: *Payment Detection*\n"
                f"• *Payment Stack:* {payments_str}\n"
                f"• *BNPL Competitor:* {has_bnpl_icon}{bnpl_detail}"
                f"{confidence_str}\n"
                f"• *Detection:* {payment_method}"
            )
        }
    })
    sections.append({"type": "divider"})

    # --- USAGE STATS ---
    sections.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f":bar_chart: *Costi (Haiku)*\n"
                f"• Questo deal: *€{usage_stats['cost_eur']:.4f}* ({usage_stats['total_tokens']:,} tokens)\n"
                f"• Oggi: *€{usage_stats['today_cost_eur']:.4f}* ({usage_stats['today_deals']} deal analizzati)"
            )
        }
    })

    # --- CONTEXT FOOTER ---
    sections.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f":robot_face: _Modello: Haiku (low-cost triage)_ | Dominio: {domain}"
            }
        ]
    })
    sections.append({"type": "divider"})

    # --- QUALIFICATION BUTTONS ---
    sections.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*🎯 Qualifica questo deal:*"
        }
    })
    sections.append({
        "type": "actions",
        "block_id": f"qualify_deal_{deal_id}",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🤖 Automated", "emoji": True},
                "value": f"{deal_id}|automated|{deal_name}",
                "action_id": "qualify_automated"
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "👤 Sales", "emoji": True},
                "value": f"{deal_id}|sales|{deal_name}",
                "action_id": "qualify_sales"
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔗 Apri in HubSpot", "emoji": True},
                "url": f"https://app-eu1.hubspot.com/contacts/26230674/record/0-3/{deal_id}",
                "action_id": "open_hubspot"
            }
        ]
    })

    payload = {
        "channel": SLACK_CHANNEL,
        "blocks": sections,
        "text": f"Quick Triage - {deal_name}"
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        result = response.json()
        if result.get("ok"):
            logger.info(f"Haiku report sent to Slack for {deal_name}")
            # Salva JSON del messaggio Slack su HubSpot (solo deal reali, non test)
            if deal_id and not deal_id.startswith("test"):
                try:
                    import json as json_mod
                    json_str = json_mod.dumps(payload, ensure_ascii=False)
                    update_hubspot_deal_property(deal_id, "sql_qualifier_json", json_str)
                    logger.info(f"JSON report salvato su HubSpot per deal {deal_id}")
                except Exception as json_err:
                    logger.warning(f"Errore salvataggio JSON su HubSpot: {json_err}")
            return True
        else:
            logger.error(f"Slack API error: {result.get('error')}")
            return False
    except Exception as e:
        logger.error(f"Failed to send Haiku Slack message: {e}")
        return False


def get_deal_info(deal_id: str) -> dict:
    """Get deal and company info from HubSpot."""
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/{deal_id}"
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}
    # Fetch iva_vat, company_domain_name and product_inbound_request from DEAL properties
    params = {"properties": "dealname,pipeline,generic_source,amount,dealstage,iva_vat,company_domain_name,product_inbound_request,category,store_type,instore_category,online_annual_revenue,offline_annual_revenue", "associations": "companies"}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        deal_data = response.json()

        deal_props = deal_data.get("properties", {})
        deal_name = deal_props.get("dealname", "Unknown")

        # Get VAT, domain, product_inbound_request, category and store_type from DEAL properties
        deal_vat = deal_props.get("iva_vat", "")
        deal_domain = deal_props.get("company_domain_name", "")
        product_request = deal_props.get("product_inbound_request", "")
        deal_category = deal_props.get("category", "")
        deal_instore_category = deal_props.get("instore_category", "")
        deal_store_type = deal_props.get("store_type", "")
        online_annual_revenue = deal_props.get("online_annual_revenue", "")
        offline_annual_revenue = deal_props.get("offline_annual_revenue", "")

        # Resolve category based on store_type:
        # E-commerce → category, Physical Store → instore_category
        if deal_store_type and "physical" in deal_store_type.lower():
            resolved_category = deal_instore_category or deal_category or ""
        else:
            resolved_category = deal_category or ""

        # Get associated company (fallback for name and other info)
        company_info = {}
        associations = deal_data.get("associations", {}).get("companies", {}).get("results", [])
        if associations:
            company_id = associations[0].get("id")
            comp_url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/companies/{company_id}"
            comp_params = {"properties": "name,domain,website,country,industry"}
            comp_response = requests.get(comp_url, headers=headers, params=comp_params)
            if comp_response.ok:
                company_info = comp_response.json().get("properties", {})

        return {
            "deal_id": deal_id,
            "deal_name": deal_name,
            "company_name": company_info.get("name", "N/A"),
            "domain": deal_domain or company_info.get("domain") or company_info.get("website", "N/A"),
            "country": company_info.get("country", "N/A"),
            "industry": company_info.get("industry", "N/A"),
            "vat": deal_vat or "N/A",
            "product_inbound_request": product_request or "N/A",
            "category": resolved_category or "N/A",
            "store_type": deal_store_type or "N/A",
            "online_annual_revenue": online_annual_revenue or "",
            "offline_annual_revenue": offline_annual_revenue or ""
        }
    except Exception as e:
        logger.error(f"Failed to get deal info: {e}")
        return {"deal_id": deal_id, "deal_name": "Unknown", "domain": "N/A", "vat": "N/A", "category": "N/A", "store_type": "N/A"}


def get_semrush_traffic(domain: str) -> str:
    """
    Get traffic data from SEMrush:
    1. domain_rank - overview (rank, organic/paid traffic)
    2. domain_organic - top keywords
    3. domain_rank across multiple databases - split by country (top 5)
    Returns formatted string for Slack.
    """
    if not domain or domain == "N/A":
        return "Dominio non disponibile per analisi traffico"

    # Clean domain
    domain = domain.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]

    traffic_info = f"*DATI SEMRUSH*\n• Dominio: {domain}\n"

    try:
        # === 1. Domain Overview (IT database) ===
        url = f"https://api.semrush.com/?type=domain_rank&key={SEMRUSH_API_KEY}&export_columns=Dn,Rk,Or,Ot,Oc,Ad,At,Ac&domain={domain}&database=it"
        response = requests.get(url, timeout=30)

        organic_traffic_it = 0
        adwords_traffic_it = 0

        if response.status_code == 200:
            text = response.text.strip()
            if text.startswith("ERROR"):
                if "NOTHING FOUND" in text:
                    traffic_info += "• :warning: Dominio non presente nel database SEMrush IT"
                else:
                    traffic_info += f"• Errore: {text}"
            else:
                lines = text.split('\n')
                if len(lines) >= 2:
                    headers_row = lines[0].split(';')
                    values_row = lines[1].split(';')
                    data = dict(zip(headers_row, values_row))

                    organic_traffic_it = int(data.get('Organic Traffic', '0') or '0')
                    adwords_traffic_it = int(data.get('Adwords Traffic', '0') or '0')
                    total_monthly = organic_traffic_it + adwords_traffic_it

                    traffic_info += f"• *Traffico Mensile (IT):* {total_monthly:,} visite/mese\n"

                    # Split Organic / Paid
                    if total_monthly > 0:
                        org_pct = (organic_traffic_it / total_monthly) * 100
                        paid_pct = (adwords_traffic_it / total_monthly) * 100
                        traffic_info += f"• Split: Organic {org_pct:.0f}% ({organic_traffic_it:,}) | Paid {paid_pct:.0f}% ({adwords_traffic_it:,})\n"
                    else:
                        traffic_info += f"• Split: Organic {organic_traffic_it:,} | Paid {adwords_traffic_it:,}\n"


        # === 2. Split by Country (top 5 databases) ===
        country_databases = [
            ("it", "Italia"), ("us", "USA"), ("uk", "UK"),
            ("fr", "Francia"), ("de", "Germania"), ("es", "Spagna")
        ]
        country_results = []

        for db_code, db_name in country_databases:
            try:
                country_url = f"https://api.semrush.com/?type=domain_rank&key={SEMRUSH_API_KEY}&export_columns=Dn,Rk,Or,Ot,Ad,At&domain={domain}&database={db_code}"
                country_resp = requests.get(country_url, timeout=15)
                if country_resp.status_code == 200 and not country_resp.text.strip().startswith("ERROR"):
                    c_lines = country_resp.text.strip().split('\n')
                    if len(c_lines) >= 2:
                        c_headers = c_lines[0].split(';')
                        c_values = c_lines[1].split(';')
                        c_data = dict(zip(c_headers, c_values))
                        org_t = int(c_data.get('Organic Traffic', '0') or '0')
                        ad_t = int(c_data.get('Adwords Traffic', '0') or '0')
                        total = org_t + ad_t
                        if total > 0:
                            country_results.append((db_name, db_code, org_t, ad_t, total))
            except Exception:
                continue

        # Sort by total traffic descending, take top 5
        country_results.sort(key=lambda x: x[4], reverse=True)

        # Highlight extra-Italy countries with significant traffic (>1K)
        top_extra = [r for r in country_results if r[1] != "it" and r[4] >= 1000]
        if top_extra:
            traffic_info += "\n:globe_with_meridians: *Traffico internazionale rilevante:*"
            for name, code, org, ad, total in top_extra[:5]:
                traffic_info += f"\n• {name}: {total:,} visite/mese"

        traffic_info += "\n\n*Split per Country (Top 5):*"
        for name, code, org, ad, total in country_results[:5]:
            traffic_info += f"\n• {name} ({code.upper()}): {total:,} visite/mese (Organic: {org:,}, Paid: {ad:,})"

        if not country_results:
            traffic_info += "\n• Nessun dato disponibile per altri paesi"

        # === 3. Top Organic Keywords ===
        kw_url = f"https://api.semrush.com/?type=domain_organic&key={SEMRUSH_API_KEY}&display_limit=5&export_columns=Ph,Po,Nq,Tr&domain={domain}&database=it"
        kw_response = requests.get(kw_url, timeout=30)

        if kw_response.status_code == 200 and not kw_response.text.startswith("ERROR"):
            kw_lines = kw_response.text.strip().split('\n')
            if len(kw_lines) > 1:
                traffic_info += "\n\n*Top Keywords Organiche:*"
                for kw_line in kw_lines[1:6]:
                    parts = kw_line.split(';')
                    if len(parts) >= 4:
                        traffic_info += f"\n• \"{parts[0]}\" - Pos: {parts[1]}, Vol: {parts[2]}, Traffic: {parts[3]}%"

        return traffic_info

    except Exception as e:
        logger.error(f"SEMrush API error for {domain}: {e}")
        return f"Errore recupero dati SEMrush: {str(e)}"


def _get_similarweb_visits(domain: str, country: str = None) -> dict:
    """
    Chiama endpoint SimilarWeb total-traffic-and-engagement/visits.
    Restituisce visite mensili e annuali per periodo corrente e precedente + YoY.
    Se country specificato (es. 'it'), filtra per quel paese.
    """
    from datetime import datetime, date, timedelta
    result = {"monthly_visits": 0, "annual_visits": 0, "prev_monthly_visits": 0, "prev_annual_visits": 0, "yoy_change": 0}

    try:
        now = datetime.now()
        # SimilarWeb ha ~2 mesi di lag: ultimo giorno di 2 mesi fa
        # Es. oggi Feb 2026 → fine periodo = 31 Dic 2025
        first_of_this_month = date(now.year, now.month, 1)
        first_of_prev_month = (first_of_this_month - timedelta(days=1)).replace(day=1)
        last_month_end = first_of_prev_month - timedelta(days=1)  # ultimo giorno di 2 mesi fa

        # Periodo corrente: 12 mesi che terminano a last_month_end
        # Es. 1 Gen 2025 → 31 Dic 2025
        if last_month_end.month == 12:
            current_start = date(last_month_end.year, 1, 1)
        else:
            current_start = date(last_month_end.year - 1, last_month_end.month + 1, 1)

        # Periodo precedente: stessi 12 mesi ma un anno prima
        # Es. 1 Gen 2024 → 31 Dic 2024
        prev_end = date(last_month_end.year - 1, last_month_end.month, last_month_end.day)
        prev_start = date(current_start.year - 1, current_start.month, current_start.day)

        def fmt(d):
            return d.strftime("%Y-%m-%d")

        base_url = f"https://api.similarweb.com/v1/website/{domain}/total-traffic-and-engagement/visits"
        params_base = f"api_key={SIMILARWEB_API_KEY}&granularity=monthly&main_domain_only=false&format=json&show_verified=false&mtd=false&engaged_only=false"
        country_param = f"&country={country}" if country else ""

        url_current = f"{base_url}?{params_base}&start_date={fmt(current_start)}&end_date={fmt(last_month_end)}{country_param}"
        url_prev = f"{base_url}?{params_base}&start_date={fmt(prev_start)}&end_date={fmt(prev_end)}{country_param}"

        scope_label = country.upper() if country else "TOTAL"
        logger.info(f"[similarweb-visits] {scope_label}: {fmt(current_start)} -> {fmt(last_month_end)}")

        resp_current = requests.get(url_current, timeout=30)
        resp_prev = requests.get(url_prev, timeout=30)

        if resp_current.status_code == 200:
            visits_list = resp_current.json().get("visits", [])
            if visits_list:
                result["annual_visits"] = sum(v.get("visits", 0) for v in visits_list)
                result["monthly_visits"] = result["annual_visits"] / len(visits_list)  # media mensile

        if resp_prev.status_code == 200:
            prev_list = resp_prev.json().get("visits", [])
            if prev_list:
                result["prev_annual_visits"] = sum(v.get("visits", 0) for v in prev_list)
                result["prev_monthly_visits"] = result["prev_annual_visits"] / len(prev_list)  # media mensile

        if result["prev_annual_visits"] > 0:
            result["yoy_change"] = round(((result["annual_visits"] - result["prev_annual_visits"]) / result["prev_annual_visits"]) * 100, 1)

        logger.info(f"[similarweb-visits] {scope_label}: monthly={result['monthly_visits']:,}, annual={result['annual_visits']:,}, YoY={result['yoy_change']}%")

    except Exception as e:
        logger.warning(f"[similarweb-visits] Errore per {domain} (country={country}): {e}")

    return result


def get_similarweb_traffic(domain: str) -> str:
    """
    Get traffic data from SimilarWeb:
    1. general-data/all - overview (rank, engagement, traffic sources, top countries)
    2. total-traffic-and-engagement/visits - split IT vs Estero con YoY
    3. similar-sites - competitor list (score >= 0.90, top 7)
    Returns formatted string for Slack.
    """
    if not domain or domain == "N/A":
        return ""

    # Clean domain
    domain = domain.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]

    traffic_info = f"\n\n*DATI SIMILARWEB*\n• Dominio: {domain}\n"

    try:
        # === 1. General Data (overview) ===
        url = f"https://api.similarweb.com/v1/website/{domain}/general-data/all?api_key={SIMILARWEB_API_KEY}&format=json"
        response = requests.get(url, timeout=30)

        monthly_visits = 0

        if response.status_code == 200:
            data = response.json()

            category = data.get("category", "N/D")
            traffic_info += f"• Categoria: {category}"

            # Engagement (tempo medio, pagine/visita, bounce rate)
            engagements = data.get("engagments", {})  # SimilarWeb typo in API
            if engagements:
                # monthly_visits snapshot (usato come fallback per calcoli assoluti)
                visits = engagements.get("visits", 0)
                if isinstance(visits, (int, float)) and visits > 0:
                    monthly_visits = visits

                time_on_site = engagements.get("time_on_site", 0)
                if isinstance(time_on_site, (int, float)) and time_on_site > 0:
                    time_str = f"{int(time_on_site // 60)}m {int(time_on_site % 60)}s"
                else:
                    time_str = "N/D"

                pages_per_visit = engagements.get("pages_per_visit", "N/D")
                bounce_rate = engagements.get("bounce_rate", "N/D")

                traffic_info += f"\n• Tempo Medio: {time_str}"
                traffic_info += f"\n• Pagine/Visita: {pages_per_visit:.1f}" if isinstance(pages_per_visit, float) else f"\n• Pagine/Visita: {pages_per_visit}"
                traffic_info += f"\n• Bounce Rate: {bounce_rate*100:.1f}%" if isinstance(bounce_rate, float) else f"\n• Bounce Rate: {bounce_rate}"

            # === Split traffico IT vs Estero (visite reali, media mensile) ===
            try:
                it_data = _get_similarweb_visits(domain, country='it')
                total_data = _get_similarweb_visits(domain, country=None)

                def _fmt_visits(n):
                    if n >= 1000000:
                        return f"{n/1000000:.1f}M"
                    elif n >= 1000:
                        return f"{n/1000:.0f}K"
                    else:
                        return f"{n:,.0f}"

                def _fmt_yoy(yoy, annual=None):
                    """Formatta YoY. Se annual e' ~0 mostra N/D (dato non significativo)."""
                    if annual is not None and annual < 100:
                        return "N/D"
                    if yoy > 0:
                        return f"+{yoy}%"
                    elif yoy < 0:
                        return f"{yoy}%"
                    else:
                        return "0%"

                if total_data["annual_visits"] > 0:
                    # Calcola non-IT
                    non_it_monthly = max(0, total_data["monthly_visits"] - it_data["monthly_visits"])
                    non_it_annual = max(0, total_data["annual_visits"] - it_data["annual_visits"])
                    prev_non_it_annual = max(0, total_data["prev_annual_visits"] - it_data["prev_annual_visits"])
                    non_it_yoy = round(((non_it_annual - prev_non_it_annual) / prev_non_it_annual) * 100, 1) if prev_non_it_annual > 0 else 0

                    traffic_info += f"\n\n*Traffico Dettagliato (visite reali):*"
                    traffic_info += f"\n• :it: *Italia:* ~{_fmt_visits(it_data['monthly_visits'])}/mese | ~{_fmt_visits(it_data['annual_visits'])}/anno | YoY: {_fmt_yoy(it_data['yoy_change'], it_data['annual_visits'])}"
                    traffic_info += f"\n• :earth_americas: *Estero:* ~{_fmt_visits(non_it_monthly)}/mese | ~{_fmt_visits(non_it_annual)}/anno | YoY: {_fmt_yoy(non_it_yoy, non_it_annual)}"
                    traffic_info += f"\n• :globe_with_meridians: *Totale:* ~{_fmt_visits(total_data['monthly_visits'])}/mese | ~{_fmt_visits(total_data['annual_visits'])}/anno | YoY: {_fmt_yoy(total_data['yoy_change'], total_data['annual_visits'])}"
                elif it_data["annual_visits"] > 0:
                    # Solo dati IT disponibili
                    traffic_info += f"\n\n*Traffico Italia (visite reali):*"
                    traffic_info += f"\n• :it: *Italia:* ~{_fmt_visits(it_data['monthly_visits'])}/mese | ~{_fmt_visits(it_data['annual_visits'])}/anno | YoY: {_fmt_yoy(it_data['yoy_change'])}"
                # Aggiorna monthly_visits con media reale (per calcoli assoluti canale/country)
                if total_data["monthly_visits"] > 0:
                    monthly_visits = total_data["monthly_visits"]
            except Exception as e:
                logger.warning(f"[similarweb-visits] Split IT/Estero fallito per {domain}: {e}")

            # Traffic sources (split by channel) - Top 5
            traffic_sources = data.get("traffic_sources", {})
            if traffic_sources:
                traffic_info += "\n\n*Split per Canale (Top 5):*"
                source_names = {
                    "search": "Search",
                    "social": "Social",
                    "direct": "Direct",
                    "referrals": "Referral",
                    "mail": "Email",
                    "paid_referrals": "Paid"
                }
                # Sort by value descending, take top 5
                sorted_sources = sorted(
                    [(name, traffic_sources.get(key, 0)) for key, name in source_names.items()],
                    key=lambda x: x[1] if isinstance(x[1], float) else 0,
                    reverse=True
                )
                for name, value in sorted_sources[:5]:
                    if isinstance(value, float) and value > 0:
                        abs_visits = int(monthly_visits * value) if monthly_visits else 0
                        abs_str = f" (~{abs_visits:,})" if abs_visits > 0 else ""
                        traffic_info += f"\n• {name}: {value*100:.1f}%{abs_str}"

            # Top countries - Top 5
            top_countries = data.get("top_country_shares", [])
            if top_countries:
                traffic_info += "\n\n*Split per Country (Top 5):*"
                for country_data in top_countries[:5]:
                    country_code = country_data.get("country", "N/D")
                    share = country_data.get("share", 0)
                    if isinstance(share, float) and share > 0:
                        abs_visits = int(monthly_visits * share) if monthly_visits else 0
                        abs_str = f" (~{abs_visits:,}/mese)" if abs_visits > 0 else ""
                        traffic_info += f"\n• {country_code.upper()}: {share*100:.1f}%{abs_str}"

        elif response.status_code == 400:
            traffic_info += "• :warning: Dominio con traffico insufficiente per SimilarWeb (dati non disponibili)"
        elif response.status_code == 404:
            traffic_info += "• :warning: Dominio non trovato nel database SimilarWeb"
        elif response.status_code == 401:
            logger.error("SimilarWeb API: Invalid API key")
            traffic_info += "• :x: API key non valida"
        else:
            logger.error(f"SimilarWeb API error: {response.status_code}")
            traffic_info += f"• Errore API: {response.status_code}"

        # === 2. Similar Sites / Competitors ===
        try:
            sim_url = f"https://api.similarweb.com/v1/website/{domain}/similar-sites/similarsites?api_key={SIMILARWEB_API_KEY}&format=json"
            sim_response = requests.get(sim_url, timeout=30)

            if sim_response.status_code == 200:
                sim_data = sim_response.json()
                similar_sites = sim_data.get("similar_sites", [])

                # Filter score >= 0.90, take top 7
                filtered_sites = [
                    site for site in similar_sites
                    if site.get("score", 0) >= 0.90
                ][:7]

                if filtered_sites:
                    traffic_info += "\n\n*Competitor / Siti Simili (score >= 0.90):*"
                    for site in filtered_sites:
                        site_url = site.get("url", "N/D")
                        site_score = site.get("score", 0)
                        traffic_info += f"\n• {site_url} (score: {site_score:.2f})"
                else:
                    # Show top 5 regardless of score if none pass filter
                    top_sites = similar_sites[:5] if similar_sites else []
                    if top_sites:
                        traffic_info += "\n\n*Competitor / Siti Simili (top 5):*"
                        for site in top_sites:
                            traffic_info += f"\n• {site.get('url', 'N/D')} (score: {site.get('score', 0):.2f})"
                    else:
                        traffic_info += "\n\n• Nessun competitor trovato"

            elif sim_response.status_code != 404:
                logger.warning(f"SimilarWeb similar-sites error: {sim_response.status_code}")
        except Exception as e:
            logger.warning(f"SimilarWeb similar-sites failed for {domain}: {e}")

        traffic_info += "\n\n_Nota: SEMrush stima il traffico potenziale dalle keyword indicizzate, SimilarWeb misura le visite reali. Per business stagionali la media annuale SimilarWeb puo' risultare inferiore._"

        return traffic_info

    except Exception as e:
        logger.error(f"SimilarWeb API error for {domain}: {e}")
        return ""


def get_wappalyzer_tech(domain: str) -> str:
    """
    Rileva tecnologie del sito tramite Wappalyzer (modalita' balanced).
    Restituisce stringa formattata Slack markdown con tecnologie raggruppate.
    """
    try:
        import warnings
        warnings.filterwarnings('ignore', category=Warning)
        from wappalyzer import analyze

        url = f"https://{domain}" if not domain.startswith("http") else domain
        logger.info(f"[wappalyzer] Scanning {url} (balanced mode)")

        results = analyze(url=url, scan_type='balanced', threads=1)

        # Prendi il primo URL nei risultati
        techs = {}
        for page_url, page_techs in results.items():
            techs = page_techs
            break

        if not techs:
            logger.info(f"[wappalyzer] Nessuna tecnologia rilevata per {domain}")
            return ""

        # Raggruppa per gruppo (Sales, Analytics, Marketing, Servers, Web development, Security, ecc.)
        groups = {}
        for tech_name, tech_info in techs.items():
            for group in tech_info.get("groups", ["Other"]):
                if group not in groups:
                    groups[group] = []
                version = tech_info.get("version", "")
                display_name = f"{tech_name} {version}".strip() if version else tech_name
                if display_name not in groups[group]:
                    groups[group].append(display_name)

        # Ordine gruppi per rilevanza nel contesto sales qualifier
        group_order = ["Sales", "Marketing", "Analytics", "Servers", "Web development", "Security", "Communication", "Media", "Content", "Other"]
        # Emoji per gruppo
        group_emoji = {
            "Sales": ":shopping_trolley:",
            "Marketing": ":mega:",
            "Analytics": ":chart_with_upwards_trend:",
            "Servers": ":globe_with_meridians:",
            "Web development": ":hammer_and_wrench:",
            "Security": ":shield:",
            "Communication": ":envelope:",
            "Media": ":movie_camera:",
            "Content": ":page_facing_up:",
            "Other": ":pushpin:",
        }

        lines = [":computer: *Tecnologie Rilevate (Wappalyzer)*"]
        shown_groups = set()

        # Prima i gruppi nell'ordine definito
        for group in group_order:
            if group in groups:
                emoji = group_emoji.get(group, ":small_blue_diamond:")
                lines.append(f"• {emoji} *{group}:* {', '.join(sorted(groups[group]))}")
                shown_groups.add(group)

        # Poi eventuali gruppi non previsti
        for group in sorted(groups.keys()):
            if group not in shown_groups:
                lines.append(f"• :small_blue_diamond: *{group}:* {', '.join(sorted(groups[group]))}")

        logger.info(f"[wappalyzer] {len(techs)} tecnologie rilevate per {domain}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[wappalyzer] Errore per {domain}: {e}")
        return ""


def trigger_agent(deal_id: str, deal_name: str, domain: str, company_name: str, vat: str = "N/A", product_request: str = "N/A", category: str = "N/A", store_type: str = "N/A", online_annual_revenue: str = "", offline_annual_revenue: str = ""):
    """Trigger the Claude agent for a specific deal."""

    # === EARLY DEDUP: blocca subito se deal già in lavorazione/inviato ===
    if deal_id in slack_message_sent:
        logger.warning(f"⚠️ Deal {deal_id} ({deal_name}) già in lavorazione o inviato, skip completo")
        return True

    # Segna IMMEDIATAMENTE come in lavorazione per prevenire race condition
    # (webhook + pending checker possono triggerare quasi simultaneamente)
    slack_message_sent[deal_id] = "processing"
    logger.info(f"Triggering Claude agent for deal: {deal_name}")

    # Set status to in_progress
    if deal_id and not deal_id.startswith("test"):
        update_hubspot_deal_property(deal_id, "sql_qualifier_status", "in_progress")

    try:
        agent_log = os.path.join(SCRIPT_DIR, "agent.log")
        slack_script = os.path.join(SCRIPT_DIR, "send_slack_report.py")

        # Ensure domain, vat and product_request are strings (handle None)
        domain = domain or "N/A"
        vat = vat or "N/A"
        product_request = product_request or "N/A"

        # Get SEMrush traffic data if domain is available
        semrush_data = ""
        similarweb_data = ""
        wappalyzer_data = ""
        if domain and domain != "N/A":
            logger.info(f"Fetching SEMrush data for: {domain}")
            semrush_data = get_semrush_traffic(domain)
            logger.info(f"SEMrush data retrieved")

            logger.info(f"Fetching SimilarWeb data for: {domain}")
            similarweb_data = get_similarweb_traffic(domain)
            logger.info(f"SimilarWeb data retrieved")

            logger.info(f"Fetching Wappalyzer tech data for: {domain}")
            wappalyzer_data = get_wappalyzer_tech(domain)
            logger.info(f"Wappalyzer data retrieved")

        # === PRE-FETCH: Revenue & Payment Stack ===
        # Quick Python fetches (free, ~1-2 sec) before Haiku triage
        logger.info(f"Fetching revenue data for: {deal_name} (VAT: {vat})")
        revenue_data = search_company_revenue(deal_name, domain, vat,
                                              hubspot_online=online_annual_revenue,
                                              hubspot_offline=offline_annual_revenue)
        logger.info(f"Revenue: {revenue_data['fatturato']} (source: {revenue_data['source'] or 'none'})")

        logger.info(f"Checking payment stack for: {domain}")
        # Use enhanced detection with agent-browser navigation + HTTP fetch (10-15 sec)
        payment_data = enhanced_payment_detection(domain)
        logger.info(f"Payment providers: {payment_data['providers']}, BNPL: {payment_data['bnpl_providers']}, Method: {payment_data['method']}, Locations: {payment_data['bnpl_locations']}")

        # === HAIKU TRIAGE ===
        # Quick triage with Haiku to decide if full Opus analysis is needed
        logger.info(f"Running Haiku triage for: {deal_name}")
        triage = triage_with_haiku(deal_name, domain, semrush_data, similarweb_data, revenue_data, payment_data, category=category, store_type=store_type, wappalyzer_data=wappalyzer_data)

        # Send Haiku report to Slack (de-duplicated)
        logger.info(f"✅ Haiku triage complete (score={triage['score']}, reason={triage['reason']})")

        # Send Slack message (dedup già garantita dall'early check all'inizio di trigger_agent)
        slack_ok = send_haiku_report_to_slack(
            triage, deal_name, deal_id, domain,
            product_request=product_request, vat=vat,
            category_hs=category, store_type=store_type,
            semrush_data=semrush_data, similarweb_data=similarweb_data,
            wappalyzer_data=wappalyzer_data,
            online_annual_revenue=online_annual_revenue,
            offline_annual_revenue=offline_annual_revenue
        )

        # Aggiorna tracking: da "processing" a True (sent)
        slack_message_sent[deal_id] = True
        logger.info(f"✅ Slack message sent and tracked for deal {deal_id}")

        # Set status to done or failed based on Slack send result
        if deal_id and not deal_id.startswith("test"):
            if slack_ok:
                update_hubspot_deal_property(deal_id, "sql_qualifier_status", "done")
            else:
                update_hubspot_deal_property(deal_id, "sql_qualifier_status", "failed")

        return True
    except Exception as e:
        logger.error(f"Failed to trigger agent: {e}")
        # Set status to failed
        if deal_id and not deal_id.startswith("test"):
            update_hubspot_deal_property(deal_id, "sql_qualifier_status", "failed")
        return False


def process_pending_deals():
    """
    Search HubSpot for deals with sql_qualifier_status = to_start, in_progress, or failed and process them.
    Called at server startup and every 10 minutes to catch deals missed while offline.
    - to_start: deals waiting to be processed
    - in_progress: deals interrupted (server crash, etc.)
    - failed: deals to retry
    """
    logger.info("[pending] Checking for deals with sql_qualifier_status = to_start/in_progress/failed...")
    try:
        url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/search"
        headers = {
            "Authorization": f"Bearer {HUBSPOT_TOKEN}",
            "Content-Type": "application/json"
        }
        # HubSpot search: use IN operator for multiple values
        payload = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "sql_qualifier_status",
                    "operator": "IN",
                    "values": ["to_start", "in_progress", "failed"]
                }]
            }],
            "properties": ["dealname", "sql_qualifier_status"],
            "limit": 50
        }

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        results = response.json().get("results", [])

        if not results:
            logger.info("[pending] No pending deals found.")
            return 0

        logger.info(f"[pending] Found {len(results)} pending deal(s)")
        processed = 0

        for deal in results:
            deal_id = deal.get("id")
            deal_name = deal.get("properties", {}).get("dealname", "Unknown")
            deal_status = deal.get("properties", {}).get("sql_qualifier_status", "unknown")
            logger.info(f"[pending] Processing: {deal_name} ({deal_id}) - status: {deal_status}")

            deal_info = get_deal_info(deal_id)
            trigger_agent(
                deal_id=deal_id,
                deal_name=deal_info.get("deal_name", "Unknown"),
                domain=deal_info.get("domain", "N/A"),
                company_name=deal_info.get("company_name", "N/A"),
                vat=deal_info.get("vat", "N/A"),
                product_request=deal_info.get("product_inbound_request", "N/A"),
                category=deal_info.get("category", "N/A"),
                store_type=deal_info.get("store_type", "N/A"),
                online_annual_revenue=deal_info.get("online_annual_revenue", ""),
                offline_annual_revenue=deal_info.get("offline_annual_revenue", "")
            )
            processed += 1

        logger.info(f"[pending] Done. Processed {processed} deal(s).")
        return processed
    except Exception as e:
        logger.error(f"[pending] Error checking pending deals: {e}")
        return 0


@app.route("/webhook/hubspot", methods=["POST"])
def hubspot_webhook():
    """Handle HubSpot webhook for deal creation."""

    # Log incoming request
    logger.info(f"Received webhook: {request.content_type}")

    # Verify signature (optional but recommended)
    signature = request.headers.get("X-HubSpot-Signature-v3", "")
    if HUBSPOT_CLIENT_SECRET and not verify_hubspot_signature(request.data, signature):
        logger.warning("Invalid webhook signature")
        return jsonify({"error": "Invalid signature"}), 401

    # Parse payload
    try:
        data = request.json
        logger.info(f"Webhook payload: {data}")
    except Exception as e:
        logger.error(f"Failed to parse payload: {e}")
        return jsonify({"error": "Invalid JSON"}), 400

    # HubSpot sends array of events
    events = data if isinstance(data, list) else [data]

    matching_deals = []
    for event in events:
        # Check if it's a deal creation event
        subscription_type = event.get("subscriptionType", "")
        object_type = event.get("objectType", "")

        if subscription_type == "deal.creation" or object_type == "deal":
            deal_id = str(event.get("objectId") or event.get("dealId", ""))
            if deal_id:
                logger.info(f"Deal creation webhook received: {deal_id}")

                # Check if deal matches our filters
                if check_deal_matches_filters(deal_id):
                    matching_deals.append(deal_id)

    # Trigger agent for each matching deal
    triggered_count = 0
    for deal_id in matching_deals:
        deal_info = get_deal_info(deal_id)
        logger.info(f"🚀 Triggering agent for deal: {deal_info.get('deal_name')}")
        trigger_agent(
            deal_id=deal_id,
            deal_name=deal_info.get("deal_name", "Unknown"),
            domain=deal_info.get("domain", "N/A"),
            company_name=deal_info.get("company_name", "N/A"),
            vat=deal_info.get("vat", "N/A"),
            product_request=deal_info.get("product_inbound_request", "N/A"),
            category=deal_info.get("category", "N/A"),
            store_type=deal_info.get("store_type", "N/A"),
            online_annual_revenue=deal_info.get("online_annual_revenue", ""),
            offline_annual_revenue=deal_info.get("offline_annual_revenue", "")
        )
        triggered_count += 1

    if not matching_deals:
        logger.info("No matching deals to process")

    return jsonify({
        "status": "ok",
        "deals_received": len(events),
        "deals_matching": len(matching_deals)
    }), 200


@app.route("/webhook/test", methods=["POST", "GET"])
def test_webhook():
    """Test endpoint to manually trigger the agent with sample data."""
    logger.info("Test webhook triggered")
    # Use sample data for testing
    trigger_agent(
        deal_id="test-123",
        deal_name="Test Deal",
        domain="scalapay.com",
        company_name="Test Company",
        vat="IT12345678901",
        product_request="Test Product Request"
    )
    return jsonify({"status": "ok", "message": "Agent triggered"}), 200


@app.route("/webhook/test-slack", methods=["POST", "GET"])
def test_slack():
    """Test endpoint to verify Slack integration."""
    logger.info("Testing Slack integration...")
    test_message = """Test Report - Deal Qualification

DEAL: Test Company
WEBSITE: example.com

ANALYSIS:
- E-commerce: Yes
- Platform: Shopify
- Fit Score: 8/10

This is a test message to verify Slack integration is working."""

    success = send_to_slack(test_message, "Test Deal")
    if success:
        return jsonify({"status": "ok", "message": "Slack message sent"}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to send Slack message - check SLACK_BOT_TOKEN"}), 500


def update_hubspot_deal_property(deal_id: str, property_name: str, property_value: str) -> bool:
    """Update a property on a HubSpot deal."""
    try:
        url = f"https://api.hubapi.com/crm/v3/objects/deals/{deal_id}"
        headers = {
            "Authorization": f"Bearer {HUBSPOT_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "properties": {
                property_name: property_value
            }
        }

        response = requests.patch(url, headers=headers, json=payload)

        if response.status_code == 200:
            logger.info(f"Updated deal {deal_id}: {property_name} = {property_value}")
            return True
        else:
            logger.error(f"Failed to update deal {deal_id}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error updating HubSpot deal: {e}")
        return False


def create_hubspot_note(deal_id: str, note_body: str) -> bool:
    """
    Crea una nota su HubSpot associata a un deal.

    Args:
        deal_id: ID del deal HubSpot
        note_body: Testo della nota

    Returns:
        True se la nota è stata creata con successo, False altrimenti
    """
    try:
        from datetime import datetime

        url = "https://api.hubapi.com/crm/v3/objects/notes"
        headers = {
            "Authorization": f"Bearer {HUBSPOT_TOKEN}",
            "Content-Type": "application/json"
        }

        # Timestamp corrente in millisecondi
        ts_ms = int(datetime.now().timestamp() * 1000)

        payload = {
            "properties": {
                "hs_note_body": note_body,
                "hs_timestamp": ts_ms
            },
            "associations": [
                {
                    "to": {"id": deal_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 214  # Note to Deal
                        }
                    ]
                }
            ]
        }

        response = requests.post(url, headers=headers, json=payload)

        if response.status_code == 201:
            logger.info(f"Created note on deal {deal_id}")
            return True
        else:
            logger.error(f"Failed to create note on deal {deal_id}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error creating HubSpot note: {e}")
        return False


@app.route("/slack/interactions", methods=["POST"])
def slack_interactions():
    """Handle Slack interactive components (button clicks)."""
    import json
    import urllib.parse
    from datetime import datetime

    # Slack sends payload as form-encoded
    payload_str = request.form.get("payload", "")
    if not payload_str:
        return jsonify({"error": "No payload"}), 400

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid payload"}), 400

    logger.info(f"Slack interaction received: {payload.get('type')}")

    # Handle button clicks
    if payload.get("type") == "block_actions":
        actions = payload.get("actions", [])
        user = payload.get("user", {}).get("name", "unknown")
        user_id = payload.get("user", {}).get("id", "")
        channel_id = payload.get("channel", {}).get("id", "")
        message_ts = payload.get("message", {}).get("ts", "")

        for action in actions:
            action_id = action.get("action_id", "")
            value = action.get("value", "")

            # Skip URL buttons (open_hubspot)
            if action_id == "open_hubspot":
                continue

            # Parse value: "deal_id|qualification|deal_name"
            if "|" in value:
                parts = value.split("|")
                deal_id = parts[0]
                qualification = parts[1]  # "Automated" or "Sales"
                deal_name = parts[2] if len(parts) > 2 else "Unknown"

                logger.info(f"User {user} qualified deal {deal_id} as {qualification}")

                # Update HubSpot
                success = update_hubspot_deal_property(deal_id, "sql_qualifier", qualification)

                # Map internal values to display names
                display_qualification = "Automated" if qualification == "automated" else "Sales"
                now = datetime.now().strftime("%d/%m/%Y alle %H:%M")

                # Create note on HubSpot
                if success:
                    note_body = f"{user} ha qualificato {deal_name} come {display_qualification} il {now}"
                    create_hubspot_note(deal_id, note_body)

                if success:
                    msg_text = f"✅ *{user}* ha qualificato *{deal_name}* come *{display_qualification}* il {now}"
                else:
                    msg_text = f"❌ Errore aggiornamento HubSpot per *{deal_name}*. Riprova."

                # Send message in thread (visible to everyone)
                if channel_id and message_ts:
                    requests.post(
                        "https://slack.com/api/chat.postMessage",
                        headers={
                            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "channel": channel_id,
                            "thread_ts": message_ts,
                            "text": msg_text
                        }
                    )

                return "", 200

    return "", 200


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    ollama_status = _check_ollama()
    return jsonify({
        "status": "healthy",
        "ollama": {
            "available": ollama_status["available"],
            "model_loaded": ollama_status["model_loaded"],
            "error": ollama_status["error"]
        }
    }), 200


@app.route("/webhook/process-pending", methods=["POST", "GET"])
def process_pending_endpoint():
    """Manual trigger to process all pending deals (to_start, in_progress, failed)."""
    count = process_pending_deals()
    return jsonify({"status": "ok", "deals_processed": count}), 200


def _start_pending_scheduler():
    """Background thread that checks for pending deals every 10 minutes."""
    import threading
    import time

    def _loop():
        while True:
            time.sleep(600)  # 10 minutes
            try:
                process_pending_deals()
            except Exception as e:
                logger.error(f"[scheduler] Error in pending deals loop: {e}")

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    logger.info("[scheduler] Background pending-deals checker started (every 10 min)")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    logger.info(f"Starting webhook server on port {port}")
    logger.info(f"Agent script: {AGENT_SCRIPT}")
    logger.info("")
    logger.info("Endpoints:")
    logger.info(f"  POST /webhook/hubspot         - HubSpot webhook receiver")
    logger.info(f"  POST /webhook/test            - Manual trigger (runs Claude)")
    logger.info(f"  GET  /webhook/test-slack      - Test Slack integration")
    logger.info(f"  GET  /webhook/process-pending - Process all pending deals (to_start/in_progress/failed)")
    logger.info(f"  POST /slack/interactions      - Slack button handler")
    logger.info(f"  GET  /health                  - Health check")
    logger.info("")
    logger.info(f"Slack Channel: {SLACK_CHANNEL}")
    logger.info(f"Slack Token: {'configured' if SLACK_BOT_TOKEN else 'NOT SET - set SLACK_BOT_TOKEN env var'}")
    logger.info("")

    # Ollama health check at startup (used for detail page extraction only)
    ollama_status = _check_ollama()
    if ollama_status["available"] and ollama_status["model_loaded"]:
        logger.info(f"Ollama: OK - server attivo, modello {OLLAMA_MODEL} disponibile")
        logger.info("  (usato solo per estrazione da pagine dettagliate, non per fallback search)")
    elif ollama_status["available"]:
        logger.warning(f"Ollama: PARZIALE - {ollama_status['error']}")
    else:
        logger.warning(f"Ollama: OFFLINE - {ollama_status['error']}")
        logger.warning("  Estrazione da pagine dettagliate non disponibile.")
        logger.warning("  Per abilitare: ollama serve && ollama pull gemma3:4b")
    logger.info("")

    # Process pending deals at startup (catch deals missed while offline)
    logger.info("Checking for pending deals (sql_qualifier_status = to_start/in_progress/failed)...")
    process_pending_deals()

    # Start background scheduler for periodic checks
    _start_pending_scheduler()

    logger.info(f"To expose publicly, run: ngrok http {port}")

    app.run(host="0.0.0.0", port=port, debug=False)
