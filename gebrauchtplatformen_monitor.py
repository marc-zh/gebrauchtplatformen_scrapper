# -*- coding: utf-8 -*- # Stellt sicher, dass Umlaute etc. korrekt interpretiert werden

import requests
from bs4 import BeautifulSoup
from time import sleep, time
from urllib.parse import quote_plus
import logging
import re
import json
import os
import html # Für HTML escaping in Telegram Nachrichten

# ==============================================================================
# 1. GLOBALE KONFIGURATION & KONSTANTEN
# ==============================================================================

# --- Dateinamen ---
CONFIG_FILE = 'monitoring_config.json'      # Datei mit den zu überwachenden Suchanfragen und Kriterien
SEEN_ITEMS_FILE = 'seen_items.json' # Datei zum Speichern der bereits gefundenen Inserate-URLs

# --- Telegram Bot Konfiguration ---
# Die Bot-Tokens sind hier nach Priorität geordnet (1=wichtig, 3=unwichtig)
TELEGRAM_BOT_TOKENS = {
    '1': "YOUR_PRIORITY_1_BOT_TOKEN_HERE",  # Wichtigster Bot
    '2': "YOUR_PRIORITY_2_BOT_TOKEN_HERE",
    '3': "YOUR_PRIORITY_3_BOT_TOKEN_HERE"   # Unwichtigster Bot
}
# Dein Bot Token von @BotFather
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID_HERE"      # Deine Chat ID

# --- Globale Grössen-Filter ---
# Hier werden die Standardgrössen für jeden Typ definiert.
# Diese werden automatisch angewendet, wenn der entsprechende Typ in der Konfigurationsdatei verwendet wird.
SIZE_FILTERS_BY_TYPE = {
    "shoes": ["42", "42.5", "42 2/3", "42. 2/3"], # Alle Varianten von 42
    "clothing": ["M", "S"],
    "macbook": ["m1", "m2", "m3", "m4"]
    # "global" braucht keinen Eintrag, da es keine Grössenfilter hat.
}

# --- Such-Parameter & Verhalten ---
CHECK_INTERVAL = 1800  # Intervall in Sekunden, in dem die Suche wiederholt wird (z.B. 300 = 5 Minuten)
BASE_URL = "https://www.deine_gebrauchtplatform.ch" # Basis-URL der zu überwachenden Seite
HEADERS = {           # User-Agent, um wie ein normaler Browser auszusehen
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
REQUEST_TIMEOUT = 20  # Maximale Wartezeit für Webseiten-Anfragen in Sekunden
INTER_REQUEST_DELAY = 1.5 # Kurze Pause zwischen einzelnen Suchanfragen (in Sekunden)
INTER_ITEM_DELAY = 3.0    # Kurze Pause nach Abarbeitung aller Suchen für ein Item (in Sekunden)
ERROR_DELAY = 5.0         # Längere Pause nach einem unerwarteten Fehler (in Sekunden)


# ==============================================================================
# 2. INITIALISIERUNG & SETUP
# ==============================================================================

# --- Telegram Verfügbarkeit prüfen ---
# Prüft, ob Tokens und Chat ID grundsätzlich gesetzt sind.
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKENS and TELEGRAM_CHAT_ID and not TELEGRAM_CHAT_ID.startswith("YOUR_"))

# --- Logging Konfiguration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)-8s - %(message)s', # Verbessertes Format
    datefmt='%Y-%m-%d %H:%M:%S'
)
# Reduziert die Ausgaben von 'requests'/'urllib3' für sauberere Logs
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)


# ==============================================================================
# 3. HILFSFUNKTIONEN (Datei-Operationen)
# ==============================================================================

def load_json_file(filename, default_value):
    """Lädt Daten sicher aus einer JSON-Datei. Gibt default_value bei Fehlern zurück."""
    if not os.path.exists(filename):
        logging.warning(f"Datei '{filename}' nicht gefunden.")
        # Erstelle eine Konfigurationsdatei aus der Vorlage, wenn sie nicht existiert.
        if filename == CONFIG_FILE:
            example_filename = filename + ".example"
            if os.path.exists(example_filename):
                try:
                    with open(example_filename, 'r', encoding='utf-8') as f_example, \
                         open(filename, 'w', encoding='utf-8') as f_new:
                        f_new.write(f_example.read())
                    logging.info(f"'{filename}' wurde aus '{example_filename}' erstellt. Bitte bearbeite sie.")
                except IOError as e:
                    logging.error(f"Fehler beim Erstellen der Konfigurationsdatei aus der Vorlage: {e}")
            else:
                logging.warning(f"Beispieldatei '{example_filename}' nicht gefunden. Erstelle eine leere Konfigurationsdatei.")
                save_json_file([], filename) # Erstellt eine leere Liste als Fallback
        # Gib trotzdem default zurück, damit das Skript korrekt anhält oder mit Defaults arbeitet
        return default_value

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip(): # Prüfe auf leeren oder nur Whitespace Inhalt
                logging.warning(f"Datei '{filename}' ist leer oder enthält nur Whitespace.")
                return default_value
            data = json.loads(content)
            # logging.info(f"{len(data)} Einträge aus '{filename}' geladen.") # Optional: Weniger verbose
            return data
    except json.JSONDecodeError as e:
        logging.error(f"Fehler beim Parsen der JSON-Datei '{filename}': {e}. Ist die Datei korrekt formatiert?")
        return default_value
    except IOError as e:
        logging.error(f"Fehler beim Lesen der Datei '{filename}': {e}")
        return default_value
    except Exception as e:
        logging.error(f"Unerwarteter Fehler beim Laden von '{filename}': {e}")
        return default_value

def save_json_file(data, filename):
    """Speichert Daten (list oder dict) sicher in eine JSON-Datei."""
    temp_filename = filename + ".tmp"
    try:
        # Temporäre Datei verwenden, um Datenverlust bei Abbruch zu minimieren
        with open(temp_filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False) # Indent 2 für Lesbarkeit
        # Wenn Schreiben erfolgreich war, ersetze die Originaldatei
        os.replace(temp_filename, filename)
        # logging.info(f"Daten sicher in '{filename}' gespeichert.") # Optional: Weniger verbose
    except IOError as e:
        logging.error(f"Fehler beim Speichern der Datei '{filename}': {e}")
    except Exception as e:
        logging.error(f"Unerwarteter Fehler beim Speichern von '{filename}': {e}")
    finally:
        # Stelle sicher, dass die temporäre Datei gelöscht wird, falls etwas schiefgeht
        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except OSError as e_rem:
                logging.error(f"Konnte temporäre Speicherdatei '{temp_filename}' nicht löschen: {e_rem}")


def load_seen_items(filename=SEEN_ITEMS_FILE):
    """Lädt die URLs der bereits gesehenen Inserate als Set."""
    content = load_json_file(filename, [])
    seen_set = set(content) # Konvertiere Liste in Set für schnelle Lookups
    logging.info(f"{len(seen_set)} bereits gesehene Inserate aus '{filename}' geladen.")
    return seen_set

def save_seen_items(seen_items_set, filename=SEEN_ITEMS_FILE):
    """Speichert die URLs der gesehenen Inserate aus einem Set."""
    save_json_file(list(seen_items_set), filename) # Konvertiere Set zurück in Liste für JSON
    logging.debug(f"{len(seen_items_set)} gesehene Inserate in '{filename}' gespeichert.") # Debug statt Info


# ==============================================================================
# 4. HILFSFUNKTIONEN (Web Scraping / Datenextraktion)
# ==============================================================================

def extract_price(listing_div):
    """
    Extrahiert den Preis aus einem Listing-Div.
    Versucht robust, verschiedene Preisformate und CSS-Selektoren zu erkennen.
    Gibt int (Preis), 0 (Gratis) oder None (nicht gefunden, VB, Anfrage) zurück.
    """
    # Mögliche Selektoren für den Preis (robust gegen kleine Änderungen)
    possible_price_selectors = [
        'div.mui-style-1fhgjcy span.mui-style-1nqm73u', # Original Selektor
        'span[class*="price"]', # Allgemeiner auf Span mit 'price' in Klasse
        'div[class*="price"]'   # Allgemeiner auf Div mit 'price' in Klasse
    ]
    price_text = None
    for selector in possible_price_selectors:
        price_tag = listing_div.select_one(selector)
        if price_tag and price_tag.text.strip():
            price_text = price_tag.text.strip().lower()
            logging.debug(f"      Preis-Text gefunden mit Selektor '{selector}': '{price_text}'")
            break # Ersten gültigen Treffer verwenden

    if not price_text:
        logging.debug("      Kein Preis-Text in den erwarteten Elementen gefunden.")
        return None

    # Prüfe auf spezielle Preisangaben
    if 'gratis' in price_text:
        logging.debug("      Preis als 'Gratis' erkannt.")
        return 0
    if any(keyword in price_text for keyword in ['anfrage', 'vb', 'verhandelbar', 'verhandlung']):
        logging.debug("      Preis als 'Auf Anfrage' oder 'VB' erkannt.")
        return None

    # Versuche, eine Zahl zu extrahieren (robust gegen Tausendertrennzeichen und Dezimalstellen)
    # Entfernt zuerst alles Nicht-Ziffern ausser dem ersten Komma/Punkt
    cleaned_for_regex = re.sub(r"[^\d,.]", "", price_text)
    # Sucht nach dem numerischen Teil vor dem ersten Komma/Punkt
    price_match = re.search(r'^(\d+)', cleaned_for_regex.replace(',', '.'))

    if price_match:
        price_str = price_match.group(1)
        try:
            price_int = int(price_str)
            logging.debug(f"      Preis extrahiert: {price_int} CHF")
            return price_int
        except ValueError:
            logging.warning(f"      Konnte extrahierten Preis-String '{price_str}' nicht in Zahl umwandeln (aus '{price_text}').")
            return None
    else:
        logging.warning(f"      Konnte keine gültige Zahl aus dem Preis-Text '{price_text}' extrahieren.")
        return None


def extract_description(listing_div):
    """Extrahiert die Kurzbeschreibung aus dem Listing-Div."""
    possible_desc_selectors = [
        'div.mui-style-xe4gv6 span.mui-style-1nqm73u', # Original spezifisch
        'div.mui-style-xe4gv6',                      # Original Container
        'p[class*="description"]',                   # Allgemeiner auf P mit 'description'
        'div[class*="description"]'                  # Allgemeiner auf Div mit 'description'
    ]
    description = ""
    for selector in possible_desc_selectors:
        desc_tag = listing_div.select_one(selector)
        if desc_tag and desc_tag.text.strip():
            description = desc_tag.text.strip()
            logging.debug(f"      Beschreibung gefunden mit Selektor '{selector}'.")
            break # Ersten Treffer verwenden
    if not description:
        logging.debug("      Keine Beschreibung gefunden.")
    return description


# ==============================================================================
# 5. FILTERFUNKTIONEN (Kategoriespezifisch)
# ==============================================================================

def check_shoe_size(title, description, target_sizes):
    """Prüft, ob eine der Ziel-Schuhgrössen in Titel oder Beschreibung vorkommt."""
    if not target_sizes: return True # Kein Filter -> immer OK
    text_to_check = (title + " " + description).lower()
    target_sizes_lower = [str(s).lower().strip() for s in target_sizes] # Sicherstellen Strings, lower, kein Whitespace

    for size_str in target_sizes_lower:
        if not size_str: continue # Leere Grössen überspringen
        # Regex sucht nach der Grösse als ganzes Wort, optional mit Präfix wie "Gr.", "EU", "Size" etc.
        size_pattern = re.escape(size_str).replace(r'\ ', r'\s*') # Erlaube variable Leerzeichen
        pattern = r'(?i)\b(?:gr\.?|eur?|size|eu|größe|grösse|groesse|us|uk|fr)?\s*' + size_pattern + r'\b(?![\d.]?[\d])'

        if re.search(pattern, text_to_check):
            logging.info(f"      -> Schuhgrössen-Filter: '{size_str.upper()}' gefunden!")
            return True # Eine passende Grösse reicht

    logging.info(f"      -> Schuhgrössen-Filter: Keine der gesuchten Grössen ({', '.join(map(str,target_sizes))}) gefunden.")
    return False

def check_clothing_size(title, description, target_sizes):
    """Prüft, ob eine der Ziel-Kleidergrössen (S, M, L etc.) in Titel oder Beschreibung vorkommt."""
    if not target_sizes: return True # Kein Filter -> immer OK
    text_to_check = (title + " " + description).lower()
    target_sizes_lower = [str(s).lower().strip() for s in target_sizes] # Sicherstellen Strings, lower, kein Whitespace

    for size_str in target_sizes_lower:
        if not size_str: continue # Leere Grössen überspringen
        # Regex sucht nach der Grösse als ganzes Wort, optional mit Präfix wie "Size", "Gr."
        pattern = r'(?i)\b(?:size|gr\.?|größe|grösse|groesse|taille|taglia)?\s*' + re.escape(size_str) + r'\b'

        if re.search(pattern, text_to_check):
            logging.info(f"      -> Kleidergrössen-Filter: '{size_str.upper()}' gefunden!")
            return True # Eine passende Grösse reicht

    logging.info(f"      -> Kleidergrössen-Filter: Keine der gesuchten Grössen ({', '.join(map(str,target_sizes))}) gefunden.")
    return False

def check_macbook_specs(title, description, target_specs):
    """Prüft, ob eine der Ziel-Spezifikationen (z.B. Chip) in Titel oder Beschreibung vorkommt."""
    if not target_specs: return True # Kein Filter -> immer OK
    text_to_check = (title + " " + description).lower()
    target_specs_lower = [str(s).lower().strip() for s in target_specs]

    for spec_str in target_specs_lower:
        if not spec_str: continue
        # Sucht nach der Spezifikation als ganzes Wort, z.B. "m1", nicht "m10"
        pattern = r'\b' + re.escape(spec_str) + r'\b'
        if re.search(pattern, text_to_check):
            logging.info(f"      -> MacBook-Spezifikation: '{spec_str.upper()}' gefunden!")
            return True # Eine passende Spezifikation reicht

    logging.info(f"      -> MacBook-Spezifikation: Keine der gesuchten Specs ({', '.join(map(str,target_specs))}) gefunden.")
    return False


# ==============================================================================
# 6. BENACHRICHTIGUNGSFUNKTION (Telegram)
# ==============================================================================

def send_telegram_notification(item_title, item_url, price, item_name, priority=3):
    """Sendet eine formatierte Nachricht über einen Fund an Telegram, basierend auf der Priorität."""
    if not TELEGRAM_ENABLED:
        if not hasattr(send_telegram_notification, "warning_logged"):
             logging.warning("Telegram ist deaktiviert. Überspringe Benachrichtigung.")
             send_telegram_notification.warning_logged = True
        return

    # Wähle den Bot-Token basierend auf der Priorität. Fallback auf Prio 3.
    bot_token = TELEGRAM_BOT_TOKENS.get(str(priority), TELEGRAM_BOT_TOKENS.get('3'))

    # Preis formatieren
    if price is None: price_str = "Preis unbekannt"
    elif price == 0: price_str = "Gratis"
    else: price_str = f"{price} CHF"

    # HTML Escaping für Sicherheit und korrekte Darstellung
    escaped_title = html.escape(item_title)
    escaped_price = html.escape(price_str)
    escaped_url = html.escape(item_url)

    message_text = (
        f"[{escaped_title}]:\n"
        f"[{escaped_price}]\n\n"
        f"{escaped_url}"
    )

    telegram_api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message_text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False # Zeigt eine Vorschau des Links an
    }

    try:
        logging.info(f"Sende Telegram Nachricht für '{item_name}': '{item_title}'")
        response = requests.post(telegram_api_url, data=payload, timeout=15) # Timeout für Senden
        response.raise_for_status()  # Löst HTTPError bei Fehlern wie 4xx/5xx aus
        response_data = response.json()

        if response_data.get('ok'):
            logging.info("  -> Telegram Nachricht erfolgreich gesendet.")
        else:
            # Detailliertere Fehlermeldung von Telegram loggen
            error_desc = response_data.get('description', 'Keine Beschreibung')
            error_code = response_data.get('error_code', 'N/A')
            logging.error(f"  -> Telegram API Fehler (Code {error_code}): {error_desc}. Payload: {payload}")
    except requests.exceptions.Timeout:
         logging.error(f"  -> Timeout Fehler beim Senden der Telegram Nachricht für: {item_url}.")
    except requests.exceptions.HTTPError as e:
         logging.error(f"  -> HTTP Fehler beim Senden der Telegram Nachricht: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"  -> Netzwerkfehler beim Senden der Telegram Nachricht: {e}")
    except Exception as e:
        logging.error(f"  -> Unerwarteter Fehler beim Senden der Telegram Nachricht für {item_url}: {e}", exc_info=True)


# ==============================================================================
# 7. KERNLOGIK: Inserate prüfen für einen Suchbegriff
# ==============================================================================

def check_single_search_term(search_term, item_config, seen_items_set):
    """
    Prüft gebrauchtplatformen.ch für EINEN spezifischen Suchbegriff und die zugehörige Item-Konfiguration.
    Extrahiert Inserate, filtert sie nach Preis und ggf. Grösse, sendet Benachrichtigungen.
    Gibt True zurück, wenn neue passende Inserate gefunden wurden, sonst False.
    """
    item_name = item_config.get("name", "Unbenanntes Item")
    item_type = item_config.get("type", "global").lower() # Typ bestimmt spezielle Filter
    max_price = item_config.get("max_price")
    target_sizes = SIZE_FILTERS_BY_TYPE.get(item_type, []) # Hole Grössen aus der globalen Konfig
    priority = item_config.get("priority", 3) # Hole Priorität, Standard ist 3 (unwichtig)

    # --- Eingabevalidierung für die Konfiguration ---
    if max_price is None:
        logging.error(f"FEHLER in Konfiguration für '{item_name}': Kein 'max_price' definiert. Überspringe Suche für '{search_term}'.")
        return False
    try:
        max_price = int(max_price)
        if max_price < 0: raise ValueError("Preis muss positiv sein.")
    except (ValueError, TypeError):
         logging.error(f"FEHLER in Konfiguration für '{item_name}': Ungültiger 'max_price' ({max_price}). Muss eine positive ganze Zahl sein. Überspringe Suche für '{search_term}'.")
         return False

    logging.info(f"---> Suche nach '{search_term}' (für Item: '{item_name}', Typ: {item_type}, MaxPreis: {max_price}, Größen: {target_sizes or 'N/A'})")

    # --- URL bauen und Seite abrufen ---
    encoded_search_term = quote_plus(search_term) # URL-Encoding für Suchbegriffe
    search_url = f"{BASE_URL}/de/q?query={encoded_search_term}"
    logging.info(f"  URL: {search_url}")

    try:
        response = requests.get(search_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status() # Fehler bei Status Codes >= 400
        logging.debug(f"    Seite für '{search_term}' erfolgreich geladen (Status: {response.status_code}).")
    except requests.exceptions.Timeout:
         logging.error(f"    Timeout ({REQUEST_TIMEOUT}s) beim Laden der Seite für '{search_term}'.")
         return False
    except requests.exceptions.HTTPError as e:
         logging.error(f"    HTTP Fehler {e.response.status_code} beim Laden der Seite für '{search_term}'.")
         return False
    except requests.exceptions.RequestException as e:
        logging.error(f"    Netzwerkfehler beim Laden der Seite für '{search_term}': {e}")
        return False
    except Exception as e:
        logging.error(f"    Unerwarteter Fehler beim Seitenabruf für '{search_term}': {e}", exc_info=True)
        return False

    # --- HTML parsen und Inserate finden ---
    soup = BeautifulSoup(response.text, 'html.parser')
    listing_selector = 'div.mui-style-qlw8p1'
    listings = soup.select(listing_selector)
    logging.info(f"    {len(listings)} potenzielle Inserate-Elemente mit Selektor '{listing_selector}' gefunden.")

    if not listings:
        # Zusätzliche Prüfung auf "Keine Ergebnisse"-Nachricht
        no_results_tag = soup.find(lambda tag: tag.name in ['div', 'p', 'h3', 'span'] and 'keine resultate' in tag.get_text(strip=True).lower())
        if no_results_tag:
            logging.info(f"    Keine Inserate gefunden (Bestätigung auf der Seite).")
        else:
            logging.warning(f"    Keine Inserate mit Selektor '{listing_selector}' gefunden und keine 'Keine Resultate'-Meldung. Seitenstruktur möglicherweise geändert?")
        return False # Keine Inserate -> keine neuen Funde

    # --- Inserate einzeln verarbeiten ---
    new_items_found_count = 0
    processed_urls_in_this_run = set() # Verhindert doppelte Verarbeitung innerhalb desselben Laufs

    for i, listing_div in enumerate(listings):
        logging.debug(f"    Verarbeite potenzielles Inserat #{i+1}...")

        # --- Link und Titel extrahieren (möglichst robust) ---
        item_title = "N/A"
        item_url = None
        link_tag = None

        possible_link_selectors = [
            'a[href^="/de/vi/"]', # Haupt-Link des Inserats
            'h2 a[href^="/de/vi/"]' # Link innerhalb des Titels
        ]
        for selector in possible_link_selectors:
            link_tag = listing_div.select_one(selector)
            if link_tag and link_tag.get('href'):
                href = link_tag['href']
                item_url = f"{BASE_URL}{href}"
                # Titel aus verschiedenen Quellen versuchen
                title_tag = listing_div.find('h2')
                item_title = title_tag.text.strip() if title_tag else link_tag.text.strip()
                if not item_title: # Fallback: Alt-Text eines Bildes
                    img_tag = link_tag.find('img', alt=True)
                    if img_tag: item_title = img_tag['alt'].strip()
                item_title = item_title or f"Inserat {i+1} (Titel nicht extrahierbar)" # Absoluter Fallback
                logging.debug(f"      Link und Titel gefunden (Selector '{selector}'): '{item_title}' -> {item_url}")
                break # Ersten gültigen Link verwenden

        if not item_url:
             external_link = listing_div.find('a', href=lambda h: h and ('ricardo.ch' in h or 'anibis.ch' in h))
             if external_link:
                  logging.debug(f"      Inserat #{i+1} ist ein externer Link ({external_link['href']}). Überspringe.")
             else:
                  logging.warning(f"      Konnte keinen gültigen Inserats-Link im Element #{i+1} finden. Überspringe.")
             continue # Nächstes Listing

        # --- Prüfen, ob schon bekannt oder doppelt in diesem Lauf ---
        if item_url in seen_items_set:
            logging.debug(f"      Inserat '{item_title}' ({item_url}) ist bereits bekannt. Überspringe.")
            continue
        if item_url in processed_urls_in_this_run:
            logging.debug(f"      Inserat '{item_title}' ({item_url}) wurde in diesem Durchlauf bereits verarbeitet. Überspringe Duplikat.")
            continue

        # === Neues, potenzielles Inserat gefunden! ===
        logging.info(f"    >> Neues potenzielles Inserat gefunden: '{item_title}'")
        logging.info(f"       URL: {item_url}")
        processed_urls_in_this_run.add(item_url) # Markieren als in diesem Lauf gesehen

        # --- Kriterien prüfen ---
        passes_filters = True # Annahme: Passt, bis ein Filter fehlschlägt

        # 1. Preis extrahieren und prüfen
        price = extract_price(listing_div)
        logging.info(f"      Prüfe Preis (Max: {max_price} CHF)...")
        if price is None:
            passes_filters = False
            logging.info(f"      -> Preisfilter FEHLGESCHLAGEN: Preis nicht extrahierbar oder 'Auf Anfrage'/'VB'.")
        elif price > max_price:
            passes_filters = False
            logging.info(f"      -> Preisfilter FEHLGESCHLAGEN: Preis ({price} CHF) ist zu hoch (Max: {max_price} CHF).")
        else:
            logging.info(f"      -> Preis ({price if price is not None else 'N/A'} CHF) OK!")

        # 2. Spezifischer Grössen-Filter (nur wenn Preis passt und Filter nötig)
        if passes_filters and item_type in ["shoes", "clothing", "macbook"] and target_sizes:
            description = extract_description(listing_div)
            logging.debug(f"      Extrahierte Beschreibung (für Grössenfilter): '{description[:100]}...'") # Log nur Anfang

            size_check_function = None
            if item_type == "shoes": size_check_function = check_shoe_size
            elif item_type == "clothing": size_check_function = check_clothing_size
            elif item_type == "macbook": size_check_function = check_macbook_specs

            if size_check_function:
                logging.info(f"      Prüfe Grössenfilter ({item_type}): {target_sizes}")
                if not size_check_function(item_title, description, target_sizes):
                    passes_filters = False
                    logging.info(f"      -> Grössenfilter FEHLGESCHLAGEN.")
                else:
                     logging.info(f"      -> Grössenfilter OK!")

        # --- Ergebnis verarbeiten ---
        if passes_filters:
            new_items_found_count += 1
            console_message = (
                f"\n✅ TREFFER! (Für Item: '{item_name}' / Suchbegriff: '{search_term}')\n"
                f"   Titel: {item_title}\n"
                f"   Preis: {price if price is not None else 'N/A'} CHF (Max: {max_price} CHF)\n"
                f"   URL: {item_url}"
            )
            if item_type in ["shoes", "clothing"] and target_sizes:
                console_message += f"\n   (Grössenfilter aktiv: {', '.join(map(str,target_sizes))})"

            # Farbige Ausgabe in der Konsole
            print("\033[92m" + "="*70 + "\033[0m") # Grüner Trenner
            print(console_message)
            print("\033[92m" + "="*70 + "\033[0m") # Grüner Trenner

            # Benachrichtigung senden und als gesehen markieren
            send_telegram_notification(item_title, item_url, price, item_name, priority)
            seen_items_set.add(item_url)
        else:
            logging.info(f"    -- Inserat '{item_title}' passt nicht zu allen Kriterien für '{item_name}'.")

    # --- Abschluss für diesen Suchbegriff ---
    if new_items_found_count > 0:
         logging.info(f"---> {new_items_found_count} neue(s) passende(s) Inserat(e) für Suchbegriff '{search_term}' gefunden und verarbeitet.")
         return True # Signalisiert, dass etwas Neues gefunden wurde
    else:
         logging.info(f"---> Keine *neuen* passenden Inserate für Suchbegriff '{search_term}' gefunden.")
         return False


# ==============================================================================
# 8. HAUPT-SCHLEIFE (Main Loop)
# ==============================================================================

def main():
    """Hauptfunktion: Lädt Konfig, startet die Endlos-Schleife zur Überwachung."""
    run_start_time = time()
    logging.info(f"--- ==== gebrauchtplatformen.ch Monitor v1.0 gestartet ==== ---") # Beispiel-Version

    # 1. Lade Konfiguration
    monitoring_config = load_json_file(CONFIG_FILE, [])
    if not monitoring_config:
        logging.critical(f"FEHLER: Konfiguration '{CONFIG_FILE}' konnte nicht geladen werden oder ist leer. Bitte erstellen/prüfen Sie die Datei.")
        logging.critical("Skript wird beendet.")
        return # Beendet das Skript, wenn keine Konfig da ist

    if not isinstance(monitoring_config, list):
         logging.critical(f"FEHLER: Der Inhalt von '{CONFIG_FILE}' muss eine Liste von Such-Objekten sein. Aktueller Typ: {type(monitoring_config)}")
         logging.critical("Skript wird beendet.")
         return

    logging.info(f"{len(monitoring_config)} Suchprofile aus '{CONFIG_FILE}' geladen.")

    # 2. Lade bereits gesehene Items
    seen_items = load_seen_items(SEEN_ITEMS_FILE)

    # 3. Telegram Status & Startnachricht
    if TELEGRAM_ENABLED:
        logging.info("Telegram Benachrichtigungen sind AKTIVIERT.")
        try:
             # Sende Startnachricht (optional, aber hilfreich)
             send_telegram_notification(
                 "gebrauchtplatformen Monitor Gestartet",
                 f"Überwachung für {len(monitoring_config)} Suchprofil(e) aktiv.",
                 None, # Kein Preis für Systemnachricht
                 "System-Status"
             )
        except Exception as e:
             logging.error(f"Fehler beim Senden der Telegram Startnachricht: {e}")
    else:
        logging.warning("Telegram Benachrichtigungen sind DEAKTIVIERT (TELEGRAM_BOT_TOKEN oder TELEGRAM_CHAT_ID ist nicht gesetzt).")

    # --- Start der periodischen Überwachung ---
    logging.info(f"Beginne periodische Überwachung alle {CHECK_INTERVAL} Sekunden. Drücke STRG+C zum Beenden.")
    cycle_count = 0
    try:
        while True:
            cycle_count += 1
            logging.info(f"--- === Beginn Suchdurchlauf #{cycle_count} === ---")
            start_cycle_time = time()
            found_new_in_cycle = False
            initial_seen_count = len(seen_items)

            # Iteriere durch jedes konfigurierte Suchprofil (Item)
            for item_config in monitoring_config:
                item_name = item_config.get("name", f"Unbenanntes Profil #{monitoring_config.index(item_config)+1}")
                search_terms = item_config.get("search_terms", [])

                if not search_terms or not isinstance(search_terms, list):
                    logging.warning(f"Überspringe Profil '{item_name}': Enthält keine gültige Liste von 'search_terms'.")
                    continue
                if not all(isinstance(term, str) and term.strip() for term in search_terms):
                    logging.warning(f"Überspringe Profil '{item_name}': 'search_terms' enthält ungültige oder leere Einträge.")
                    continue

                logging.debug(f"Verarbeite Profil: '{item_name}' mit {len(search_terms)} Suchbegriff(en)...")

                # Iteriere durch die Suchbegriffe für dieses Profil
                item_found_new = False # Flag, ob für DIESES Item etwas Neues gefunden wurde
                for search_term in search_terms:
                    try:
                        # Führe die Suche und Filterung für diesen Begriff aus
                        if check_single_search_term(search_term.strip(), item_config, seen_items):
                            item_found_new = True # Markieren, dass etwas gefunden wurde
                            found_new_in_cycle = True # Markieren für den gesamten Zyklus
                            # Speichere nach jedem Fund, um Datenverlust zu minimieren
                            save_seen_items(seen_items, SEEN_ITEMS_FILE)

                        # Kurze Pause zwischen den einzelnen Suchanfragen
                        sleep(INTER_REQUEST_DELAY)

                    except Exception as e:
                         # Fängt unerwartete Fehler innerhalb der Verarbeitung eines Suchbegriffs ab
                         logging.error(f"!! Unerwarteter Fehler bei Verarbeitung von '{search_term}' für '{item_name}': {e}", exc_info=True)
                         logging.error("   -> Fahre mit nächstem Suchbegriff/Profil fort.")
                         sleep(ERROR_DELAY) # Längere Pause nach einem Fehler

                if item_found_new:
                    logging.info(f"-> Neue(s) Inserat(e) für Profil '{item_name}' in diesem Durchlauf gefunden.")

                # Kleine Pause nach Abarbeitung aller Suchbegriffe eines Items/Profils
                sleep(INTER_ITEM_DELAY)

            # --- Abschluss des gesamten Suchdurchlaufs ---
            cycle_duration = time() - start_cycle_time
            newly_added_count = len(seen_items) - initial_seen_count

            logging.info(f"--- === Suchdurchlauf #{cycle_count} abgeschlossen ({cycle_duration:.2f}s) === ---")
            if newly_added_count > 0:
                logging.info(f"   >>> {newly_added_count} neue passende Inserate insgesamt in diesem Durchlauf gefunden und gespeichert.")
            else:
                logging.info("   Keine *neuen* passenden Inserate in diesem Durchlauf gefunden.")
                # Sende eine "Nichts gefunden"-Nachricht nur an die Konsole/Logs, nicht an Telegram
                # Die Telegram-Nachrichten werden nur bei tatsächlichen Funden gesendet

            logging.info(f"Gesamtzahl überwachter (gesehener) Inserate: {len(seen_items)}")
            logging.info(f"Warte {CHECK_INTERVAL} Sekunden bis zum nächsten Durchlauf...")
            sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print() # Neue Zeile nach ^C
        logging.info("🛑 Skript durch Benutzer (STRG+C) unterbrochen.")
    except Exception as e:
        # Fängt unerwartete Fehler auf der obersten Ebene der Schleife ab
        logging.critical(f"💥 Kritischer Fehler in der Hauptschleife: {e}", exc_info=True)
        if TELEGRAM_ENABLED:
             try:
                 send_telegram_notification("gebrauchtplatformen Monitor KRITISCHER FEHLER", f"Fehler: {e}\nSkript wird möglicherweise beendet.", None, "System-Alarm")
             except Exception as te:
                 logging.error(f"Konnte Telegram-Fehlermeldung nicht senden: {te}")
    finally:
        # Wird immer ausgeführt, auch bei Fehlern oder Abbruch
        logging.info("--- Skript wird beendet. Führe abschliessende Aktionen durch... ---")
        try:
             # Stelle sicher, dass der letzte Stand der gesehenen Items gespeichert wird
             logging.info(f"Speichere {len(seen_items)} gesehene Items in '{SEEN_ITEMS_FILE}'...")
             save_seen_items(seen_items, SEEN_ITEMS_FILE)
             logging.info("Speichern erfolgreich.")
        except Exception as e:
             logging.error(f"Fehler beim finalen Speichern von '{SEEN_ITEMS_FILE}': {e}")

        run_end_time = time()
        total_runtime = run_end_time - run_start_time
        logging.info(f"--- ==== gebrauchtplatformen.ch Monitor beendet nach {total_runtime:.2f} Sekunden ==== ---")


# ==============================================================================
# 9. SKRIPT STARTPUNKT
# ==============================================================================

if __name__ == "__main__":
    main()
