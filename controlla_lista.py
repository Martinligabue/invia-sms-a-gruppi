#!/usr/bin/env python3
"""
controlla_lista.py - controlla chi di un elenco (CSV o xls) e' gia' in un gruppo Telegram.

Non modifica mai il gruppo. Legge i membri, confronta l'elenco e scrive un report .md con:
presenti, da aggiungere e problemi. Per ogni riga usa lo USERNAME; se vuoi puo' provare anche il
NUMERO DI TELEFONO (opzione --telefono, vedi l'avviso sulla privacy in --help).

Serve:  pip install telethon     (e xlrd solo per i file .xls)
        config.ini con due righe:   id=123456   /   hash=0123456789abcdef0123456789abcdef
        (api_id e api_hash si ottengono su https://my.telegram.org -> API development tools)

Aiuto completo:  python controlla_lista.py --help
"""
import argparse
import asyncio
import csv
import json
import random
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    ChatAdminRequiredError,
    FloodWaitError,
    RPCError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.contacts import (
    DeleteContactsRequest,
    GetContactsRequest,
    ImportContactsRequest,
    ResolveUsernameRequest,
)
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import ChatInviteAlready, InputPhoneContact, PeerUser

MAX_FLOOD_WAIT = 300   # attese di Telegram piu' lunghe di cosi': mi fermo e salvo
PAUSA = (1.0, 2.5)     # pausa casuale tra una risoluzione di username e l'altra
CHUNK = 10             # numeri importati per richiesta
USER_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
SCI = re.compile(r"^\d+([.,]\d+)?[eE][+-]?\d+$")
PERMANENTI = {"utente", "bot", "non_utente", "eliminato"}   # esiti in cache che non scadono
NO_USERNAME = {"senza", "formato_non_valido"}

TITOLI_PROBLEMI = [
    ("non_su_telegram", "Non trovati su Telegram (username inesistente/assente e numero non trovato)"),
    ("bot_o_canale", "Lo username e' un bot, un canale o un account eliminato (e il numero non ha dato nulla)"),
    ("senza_dati", "Senza username ne' numero utilizzabili"),
    ("errore", "Errori imprevisti"),
    ("non_controllato", "Non controllati (limite di Telegram, interruzione o ricerca per telefono non attiva)"),
]


# ------------------------------------------------------- funzioni di appoggio ---
def leggi_config(path):
    p = Path(path)
    if not p.exists():
        sys.exit(f"Manca {path}: servono due righe, 'id=...' e 'hash=...' (vedi --help)")
    cfg = {}
    for riga in p.read_text(encoding="utf-8-sig").splitlines():
        riga = riga.strip()
        if "=" in riga and not riga.startswith(("#", ";", "[")):
            k, v = riga.split("=", 1)
            cfg[k.strip().lower()] = v.strip().strip("\"'")
    api_id = cfg.get("id") or cfg.get("api_id")
    api_hash = cfg.get("hash") or cfg.get("api_hash")
    if not (api_id and api_hash and api_id.isdigit()):
        sys.exit("config.ini: servono 'id' (solo cifre) e 'hash'")
    return int(api_id), api_hash


def cella(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def pulisci(raw):
    s = re.sub(r"^(https?://)?(t|telegram)\.me/", "", raw.strip(), flags=re.I)
    return s.lstrip("@").strip()


def prefiltro(u):
    """None se lo username e' plausibile, altrimenti il motivo dello scarto."""
    if not u:
        return "senza_username"
    if re.search(r"\s", u):
        return "non_username_sembra_nome"
    if u.isdigit():
        return "non_username_numerico"
    if not USER_RE.match(u):
        return "formato_non_valido"
    return None


def tokens(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return frozenset(re.findall(r"[a-z]+", s))


def numero(raw, prefisso):
    """(numero in formato +..., motivo se non utilizzabile)."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    if SCI.match(raw):
        return "", "numero in notazione scientifica (il CSV ha perso le cifre: formatta la colonna come testo)"
    s = re.sub(r"[^\d+]", "", raw)
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        if not prefisso:
            return "", f"numero senza prefisso internazionale: «{raw}» (usa --prefisso)"
        s = prefisso + s
    return (s, "") if re.fullmatch(r"\+\d{8,15}", s) else ("", f"numero non valido: «{raw}»")


async def trova_gruppo(client, link):
    m = re.search(r"(?:t|telegram)\.me/(?:joinchat/|\+)([\w-]+)", link)
    try:
        if m:
            inv = await client(CheckChatInviteRequest(m.group(1)))
            if isinstance(inv, ChatInviteAlready):
                return inv.chat
            sys.exit(f"Non risulti membro di «{getattr(inv, 'title', '?')}». Entra col link dal tuo "
                     "account Telegram e rilancia (lo script e' in sola lettura: non entra da solo).")
        return await client.get_entity(link)
    except RPCError as e:
        sys.exit(f"Impossibile aprire il gruppo: {e}")


async def scarica_membri(client, gruppo):
    membri = {}
    try:
        async for u in client.iter_participants(gruppo):
            nome = " ".join(x for x in (u.first_name, u.last_name) if x)
            membri[u.id] = {"id": u.id, "nome": nome or "(senza nome)", "username": u.username, "tok": tokens(nome)}
        dichiarati = (await client.get_participants(gruppo, limit=0)).total
    except ChatAdminRequiredError:
        sys.exit("La lista membri non e' visibile ai non-admin di questo gruppo: "
                 "lancia il controllo con l'account di un amministratore.")
    except RPCError as e:
        sys.exit(f"Errore nel leggere i membri: {e}")
    return membri, dichiarati


async def risolvi(client, uname):
    for _ in range(3):
        try:
            res = await client(ResolveUsernameRequest(uname))
        except UsernameNotOccupiedError:
            return {"stato": "inesistente"}
        except UsernameInvalidError:
            return {"stato": "formato_non_valido"}
        except FloodWaitError as e:
            if e.seconds > MAX_FLOOD_WAIT:
                raise
            print(f"   FloodWait: aspetto {e.seconds}s...")
            await asyncio.sleep(e.seconds + 1)
            continue
        if isinstance(res.peer, PeerUser):
            u = next((x for x in res.users if x.id == res.peer.user_id), None)
            if u is not None:
                if u.deleted:
                    return {"stato": "eliminato", "id": u.id}
                if u.bot:
                    return {"stato": "bot", "id": u.id}
                return {"stato": "utente", "id": u.id}
        return {"stato": "non_utente"}
    return {"stato": "errore", "dettaglio": "troppi FloodWait consecutivi"}


# ------------------------------------------------------------------ input ---
def leggi_testo(path):
    dati = Path(path).read_bytes()
    try:
        return dati.decode("utf-8-sig")
    except UnicodeDecodeError:
        return dati.decode("cp1252")


def righe_grezze(p):
    if p.suffix.lower() == ".xls":
        try:
            import xlrd
        except ImportError:
            sys.exit("Per i file .xls serve:  pip install xlrd   (oppure salva l'elenco come CSV)")
        sh = xlrd.open_workbook(str(p)).sheet_by_index(0)
        return [[cella(c) for c in sh.row_values(r)] for r in range(sh.nrows)]
    if p.suffix.lower() in (".xlsx", ".ods"):
        sys.exit("Formato non supportato: salva l'elenco come CSV (o .xls).")
    testo = leggi_testo(p)
    prima = testo.splitlines()[0] if testo.strip() else ""
    delim = ";" if prima.count(";") >= prima.count(",") else ","
    return list(csv.reader(testo.splitlines(), delimiter=delim))


def trova_col(head, esplicita, chiavi, esatto=False):
    if esplicita:
        for i, h in enumerate(head):
            if h == esplicita.strip().lower():
                return i
        sys.exit(f"Colonna '{esplicita}' non trovata. Intestazioni: {head}")
    for k in chiavi:
        for i, h in enumerate(head):
            if (h == k) if esatto else (k in h):
                return i
    return None


def leggi_lista(path, col_u, col_t, prefisso):
    p = Path(path)
    if not p.exists():
        sys.exit(f"Non trovo {path}")
    righe = righe_grezze(p)
    if not righe:
        sys.exit("Il file e' vuoto")
    intest = righe[0]
    head = [h.strip().lower() for h in intest]
    iu = trova_col(head, col_u, ["username"])
    it = trova_col(head, col_t, ["cellulare", "telefono", "numero", "tel"])
    ic = trova_col(head, None, ["cognome"], esatto=True)
    inm = trova_col(head, None, ["nome"], esatto=True)
    if iu is None and it is None:
        sys.exit(f"Non trovo le colonne username / telefono (vedi --col-username, --col-telefono). Intestazioni: {head}")
    get = lambda r, i: r[i].strip() if i is not None and i < len(r) else ""
    out = []
    for n, r in enumerate(righe[1:], start=2):
        if any(c.strip() for c in r):
            out.append({"riga": n, "cognome": get(r, ic), "nome": get(r, inm),
                        "u_raw": get(r, iu), "t_raw": get(r, it), "orig": r})
    for r in out:
        r["u"] = pulisci(r["u_raw"])
        r["pre"] = prefiltro(r["u"]) if r["u"] else "senza_username"
        r["tel"], r["tel_problema"] = numero(r["t_raw"], prefisso)
    return out, intest


# ------------------------------------------------------------------ cache ---
def oggi():
    return date.today().isoformat()


def carica_cache(path):
    p = Path(path)
    cache = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    cache.setdefault("username", {})
    cache.setdefault("telefono", {})
    return cache


def importa_cache(cache, path):
    """Aggiunge a cache le voci di un altro file di cache (formato nuovo o {username: {stato, id}})."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"Non trovo il file di cache {path}")
    dati = json.loads(p.read_text(encoding="utf-8"))
    quando = datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()
    sezioni = dati if "username" in dati or "telefono" in dati else {"username": dati}
    n = 0
    for sez, voci in sezioni.items():
        for k, e in (voci or {}).items():
            if sez in cache and isinstance(e, dict) and e.get("stato") and k not in cache[sez]:
                cache[sez][k] = {**e, "quando": e.get("quando", quando)}
                n += 1
    print(f"Importate {n} voci da {path}")


def salva_cache(path, cache):
    Path(path).write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def in_cache(sez, chiave, args):
    e = sez.get(chiave)
    if not e:
        return None
    if args.ricontrolla:  # rifa' tutto cio' che non e' gia' stato aggiornato oggi
        return e if e.get("quando") == oggi() else None
    if e["stato"] in PERMANENTI:
        return e
    eta = (date.today() - date.fromisoformat(e.get("quando", oggi()))).days
    return e if eta < args.scadenza_giorni else None


# ------------------------------------------------------------------ fasi ---
async def fase_username(client, righe, cache, args):
    da_fare, visti = [], set()
    for r in righe:
        k = r["u"].lower()
        if r["pre"] is None and k not in visti and in_cache(cache["username"], k, args) is None:
            visti.add(k)
            da_fare.append(r["u"])
    print(f"\nUSERNAME: {len(da_fare)} da chiedere a Telegram (gli altri sono in cache)")
    try:
        for i, u in enumerate(da_fare, 1):
            try:
                res = await risolvi(client, u)
            except FloodWaitError as e:
                print(f"\nTelegram chiede {e.seconds}s di attesa per gli username: mi fermo qui.")
                return True
            except Exception as e:
                print(f"[{i}/{len(da_fare)}] @{u} -> errore {type(e).__name__}")
                continue
            if res["stato"] == "errore":
                continue
            cache["username"][u.lower()] = {**res, "quando": oggi()}
            print(f"[{i}/{len(da_fare)}] @{u} -> {res['stato']}")
            if i % 10 == 0:
                salva_cache(args.cache, cache)
            await asyncio.sleep(random.uniform(*PAUSA))
    finally:
        salva_cache(args.cache, cache)
    return False


async def fase_telefono(client, numeri, nomi, cache, args):
    da_fare = [n for n in numeri if in_cache(cache["telefono"], n, args) is None]
    print(f"\nTELEFONO: {len(da_fare)} numeri da cercare (gli altri sono in cache)")
    if not da_fare:
        return False
    res = await client(GetContactsRequest(0))
    prima = {}
    for u in getattr(res, "users", []) or []:
        if getattr(u, "phone", None):
            prima["+" + u.phone.lstrip("+")] = u
    prima_ids = {u.id for u in prima.values()}
    fatti, cid = 0, 0
    try:
        for i in range(0, len(da_fare), CHUNK):
            if fatti >= args.max_numeri:
                print("Raggiunto il limite di numeri per questa esecuzione (--max-numeri).")
                return True
            chunk = da_fare[i: i + min(CHUNK, args.max_numeri - fatti)]
            fatti += len(chunk)
            for n in chunk:
                if n in prima:
                    cache["telefono"][n] = {"stato": "utente", "id": prima[n].id, "quando": oggi()}
            da_imp = [n for n in chunk if n not in prima]
            nuovi = []
            if da_imp:
                contatti, mappa = [], {}
                for n in da_imp:
                    cid += 1
                    mappa[cid] = n
                    nome, cogn = nomi.get(n, ("", ""))
                    contatti.append(InputPhoneContact(
                        client_id=cid, phone=n, first_name=(nome or cogn or "tmp").title(),
                        last_name=cogn.title() if nome else ""))
                for tentativo in range(2):
                    try:
                        ris = await client(ImportContactsRequest(contatti))
                        break
                    except FloodWaitError as e:
                        if e.seconds > MAX_FLOOD_WAIT or tentativo:
                            print(f"\nTelegram chiede {e.seconds}s di attesa sui contatti: mi fermo qui.")
                            return True
                        print(f"   FloodWait: aspetto {e.seconds}s...")
                        await asyncio.sleep(e.seconds + 1)
                imp = {c.client_id: c.user_id for c in ris.imported}
                riprova = set(ris.retry_contacts or [])
                nuovi = [u for u in ris.users if u.id not in prima_ids]
                for c, n in mappa.items():
                    if c in imp:
                        cache["telefono"][n] = {"stato": "utente", "id": imp[c], "quando": oggi()}
                    elif c not in riprova:
                        cache["telefono"][n] = {"stato": "non_trovato", "quando": oggi()}
                if nuovi:  # i contatti temporanei non devono restare nella rubrica
                    try:
                        await client(DeleteContactsRequest(id=nuovi))
                    except RPCError as e:
                        print(f"   (non sono riuscito a cancellare i contatti temporanei: {e})")
            for n in chunk:
                e = cache["telefono"].get(n)
                if e:
                    print(f"[tel {fatti}/{len(da_fare)}] ...{n[-3:]} -> {e['stato']}")
            salva_cache(args.cache, cache)
            await asyncio.sleep(random.uniform(3.0, 6.0))
    finally:
        salva_cache(args.cache, cache)
    return False


# ----------------------------------------------------------- classificazione ---
def somiglianti(r, membri):
    """Membri il cui nome contiene nome e cognome della riga (confronto approssimato)."""
    t = tokens(f"{r['nome']} {r['cognome']}")
    if len(t) < 2:
        return ""
    trovati = [m for m in membri.values() if t <= m["tok"]][:3]
    return "; ".join(m["nome"] + (f" (@{m['username']})" if m["username"] else "") for m in trovati)


def classifica(righe, cache, membri, args):
    visti_id = {}
    for r in righe:
        ru = in_cache(cache["username"], r["u"].lower(), args) if r["pre"] is None else None
        rt = in_cache(cache["telefono"], r["tel"], args) if r["tel"] and args.telefono else None
        if r["pre"] is not None:
            u_stato = "senza" if r["pre"] == "senza_username" else "formato_non_valido"
        else:
            u_stato = ru["stato"] if ru else "non_controllato"
        if not r["tel"]:
            t_stato = "senza"
        elif not args.telefono:
            t_stato = "non_controllato"
        else:
            t_stato = rt["stato"] if rt else "non_controllato"
        r["note"] = [r["tel_problema"]] if r["tel_problema"] else []
        if r["pre"] == "formato_non_valido" or (r["pre"] and r["pre"].startswith("non_username")):
            r["note"].append(f"campo username non valido: «{r['u_raw']}»")
        if u_stato == "formato_non_valido" and r["pre"] is None:
            r["note"].append("username non valido per Telegram")
        r["id"], r["via"], r["indizio"] = None, "", ""
        if u_stato == "utente":
            r["id"], r["via"] = ru["id"], "username"
        elif t_stato == "utente":
            r["id"], r["via"] = rt["id"], "numero"
        if r["id"]:
            r["stato"] = "presente" if r["id"] in membri else "da_aggiungere"
            if r["stato"] == "da_aggiungere" and somiglianti(r, membri):
                r["note"].append("attenzione, nel gruppo c'e' gia' un membro con lo stesso nome: "
                                 + somiglianti(r, membri))
            if r["id"] in visti_id:
                r["note"].append(f"stesso account della riga {visti_id[r['id']]}")
            visti_id.setdefault(r["id"], r["riga"])
            continue
        if "non_controllato" in (u_stato, t_stato):
            r["stato"] = "non_controllato"
        elif u_stato in NO_USERNAME and t_stato == "senza":
            r["stato"] = "senza_dati"
        elif u_stato in ("bot", "non_utente", "eliminato"):
            r["stato"] = "bot_o_canale"
            r["note"].append({"bot": "lo username e' un bot", "non_utente": "lo username e' un canale o un gruppo",
                              "eliminato": "account eliminato"}[u_stato])
        else:
            r["stato"] = "non_su_telegram"
            if u_stato == "inesistente":
                r["note"].append("username inesistente")
            if t_stato == "non_trovato":
                r["note"].append("numero non trovato su Telegram")
        if r["stato"] in ("non_su_telegram", "bot_o_canale", "senza_dati") and somiglianti(r, membri):
            r["stato"] = "forse_presente"
            r["indizio"] = somiglianti(r, membri)


# ----------------------------------------------------------------- output ---
def maschera(t):
    return f"{t[:3]}…{t[-3:]}" if t else ""


def riga_md(r, args):
    nome = f"{r['cognome']} {r['nome']}".strip() or f"riga {r['riga']}"
    s = f"- {nome}"
    if r["u"] and r["pre"] is None:
        s += f" — `@{r['u']}`"
    if r["tel"] and (r["via"] != "username" or r["stato"] != "presente"):
        s += f" — {maschera(r['tel']) if args.maschera_numeri else r['tel']}"
    extra = list(r["note"])
    if r["indizio"]:
        extra.append(f"forse e' gia' nel gruppo come: {r['indizio']}")
    if extra:
        s += " — _" + " · ".join(extra) + "_"
    return s + f" (riga {r['riga']})"


def scrivi_md(path, righe, titolo, n_membri, args):
    def sel(stato, via=None):
        return sorted((r for r in righe if r["stato"] == stato and (via is None or r["via"] == via)),
                      key=lambda r: (r["cognome"].lower(), r["nome"].lower(), r["riga"]))

    out = [f"# Controllo lista — «{titolo}»",
           f"_Generato il {datetime.now():%d/%m/%Y %H:%M}. Membri letti dal gruppo: {n_membri}. "
           "Il gruppo non e' stato modificato._"]
    if not args.telefono:
        out.append("\n_Controllo solo per username: la ricerca per telefono non e' attiva (--telefono)._")
    pres, agg = sel("presente"), sel("da_aggiungere")
    out += ["\n## Riepilogo\n", f"- Righe in lista: {len(righe)}",
            f"- Già nel gruppo: {len(pres)} ({sum(r['via'] == 'username' for r in pres)} via username, "
            f"{sum(r['via'] == 'numero' for r in pres)} via numero)",
            f"- Forse già nel gruppo (per nome): {len(sel('forse_presente'))}",
            f"- Da aggiungere: {len(agg)} ({sum(r['via'] == 'username' for r in agg)} via username, "
            f"{sum(r['via'] == 'numero' for r in agg)} via numero)"]
    out += [f"- {t}: {len(sel(k))}" for k, t in TITOLI_PROBLEMI if sel(k)]

    out.append("\n## Presenti")
    for tit, lst in (("Già nel gruppo", pres),
                     ("Forse già nel gruppo (per nome, da verificare)", sel("forse_presente"))):
        if lst:
            out += [f"\n### {tit} ({len(lst)})\n"] + [riga_md(r, args) for r in lst]
    out.append("\n## Da aggiungere")
    for tit, via in (("Trovati via username", "username"), ("Trovati via numero di telefono", "numero")):
        lst = sel("da_aggiungere", via)
        if lst:
            out += [f"\n### {tit} ({len(lst)})\n"] + [riga_md(r, args) for r in lst]
    out.append("\n## Problemi")
    for k, t in TITOLI_PROBLEMI:
        lst = sel(k)
        if lst:
            out += [f"\n### {t} ({len(lst)})\n"] + [riga_md(r, args) for r in lst]
    out.append("\n_Gli indizi «forse è già nel gruppo» sono un confronto approssimato nome/cognome._")
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


def scrivi_csv_stati(path, righe, intest):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(list(intest) + ["stato", "via", "telegram_id", "note"])
        for r in righe:
            orig = list(r["orig"]) + [""] * (len(intest) - len(r["orig"]))
            w.writerow(orig + [r["stato"], r["via"], r["id"] or "", " · ".join(r["note"])])


# ------------------------------------------------------------------- main ---
async def elabora(client, righe, membri, args):
    cache = carica_cache(args.cache)
    if args.importa_cache:
        importa_cache(cache, args.importa_cache)
    await fase_username(client, righe, cache, args)
    if args.telefono:
        numeri, nomi = [], {}
        for r in righe:  # numeri delle righe che lo username non ha identificato come utente
            e = in_cache(cache["username"], r["u"].lower(), args) if r["pre"] is None else None
            if r["tel"] and not (e and e["stato"] == "utente") and r["tel"] not in nomi:
                numeri.append(r["tel"])
                nomi[r["tel"]] = (r["nome"], r["cognome"])
        await fase_telefono(client, numeri, nomi, cache, args)
    classifica(righe, cache, membri, args)


def costruisci_parser():
    ap = argparse.ArgumentParser(
        prog="controlla_lista.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Controlla chi di un elenco (CSV o xls) e' gia' in un gruppo Telegram.\n"
            "Non modifica mai il gruppo. Scrive un report .md con presenti, da aggiungere e problemi.\n\n"
            "FORMATO DELL'ELENCO\n"
            "  Prima riga = intestazioni. Colonne cercate da sole (anche con altri nomi simili):\n"
            "    username   (facoltativa se c'e' il telefono)   telefono / cellulare / numero\n"
            "    nome, cognome (facoltative: servono al report e al confronto per somiglianza di nome)\n"
            "  Separatore ';' o ','. Il telefono va formattato come testo (Excel rovina i numeri lunghi).\n\n"
            "COME FUNZIONA\n"
            "  1. legge i membri del gruppo (serve esserne membri; se la lista e' nascosta, un admin)\n"
            "  2. risolve ogni username in un ID e lo confronta con i membri\n"
            "  3. con --telefono: per le righe non identificate dallo username cerca il numero\n"
            "  4. ricorda ogni ricerca in una cache: rilanciando non rifa' le richieste"),
        epilog=(
            "ESEMPI\n"
            "  python controlla_lista.py --lista elenco.csv --gruppo 'https://t.me/+XXXXXXXX'\n"
            "  python controlla_lista.py --lista elenco.csv --gruppo '...' --telefono --prefisso +39\n"
            "  python controlla_lista.py --lista elenco.xls --gruppo '...' --limit 10     # prova su 10 righe\n\n"
            "NOTE\n"
            "  - Il primo avvio chiede numero, codice (arriva nell'app Telegram) ed eventuale 2FA e crea\n"
            "    un file di sessione (*.session): equivale a una password, non condividerlo.\n"
            "  - Telegram limita le ricerche (circa 200 username al giorno): se impone un'attesa lunga lo\n"
            "    script si ferma, salva la cache e scrive il report parziale. Rilancia piu' tardi.\n"
            "  - PRIVACY: --telefono importa temporaneamente i numeri tra i contatti per scoprire chi e'\n"
            "    su Telegram (poi li cancella). Rivela se un numero ha un account: usalo solo su elenchi di\n"
            "    persone che hanno dato il numero per questo scopo, nel rispetto del GDPR e dei termini di\n"
            "    Telegram. Elenco e report contengono dati personali: tienili privati."))
    g = ap.add_argument_group("elenco")
    g.add_argument("--lista", required=True, metavar="FILE", help="elenco in CSV o xls (obbligatorio)")
    g.add_argument("--col-username", metavar="NOME", help="forza il nome della colonna username")
    g.add_argument("--col-telefono", metavar="NOME", help="forza il nome della colonna telefono")
    g.add_argument("--prefisso", metavar="+NN", help="prefisso internazionale per i numeri scritti senza "
                   "«+» (es. +39). Senza, quei numeri vengono scartati")
    g.add_argument("--limit", type=int, metavar="N", help="elabora solo le prime N righe (per prova)")
    g = ap.add_argument_group("telegram")
    g.add_argument("--gruppo", required=True, metavar="LINK", help="link d'invito (t.me/+...) o pubblico del gruppo")
    g.add_argument("--config", default="config.ini", metavar="FILE",
                   help="file con id= e hash= (default: config.ini)")
    g.add_argument("--sessione", default="sessione_controllo", metavar="NOME",
                   help="nome del file di sessione (default: sessione_controllo)")
    g = ap.add_argument_group("ricerca per telefono (facoltativa)")
    g.add_argument("--telefono", action="store_true",
                   help="cerca per numero le righe che lo username non identifica (vedi avviso privacy)")
    g.add_argument("--max-numeri", type=int, default=150, metavar="N",
                   help="numeri da cercare al massimo per esecuzione (default: 150)")
    g = ap.add_argument_group("cache")
    g.add_argument("--cache", default="cache_telegram.json", metavar="FILE",
                   help="file di cache (default: cache_telegram.json)")
    g.add_argument("--scadenza-giorni", type=int, default=14, metavar="N",
                   help="dopo quanti giorni ricontrollare gli esiti negativi (default: 14)")
    g.add_argument("--ricontrolla", action="store_true",
                   help="rifa' tutte le ricerche non ancora aggiornate oggi, ignorando la cache")
    g.add_argument("--importa-cache", metavar="FILE", help="aggiunge le voci di un altro file di cache")
    g = ap.add_argument_group("risultati")
    g.add_argument("--out", default="controllo_lista.md", metavar="FILE", help="report (default: controllo_lista.md)")
    g.add_argument("--csv-stati", metavar="FILE",
                   help="scrive anche una copia dell'elenco con le colonne stato, via, telegram_id, note "
                        "(l'elenco originale non viene mai modificato)")
    g.add_argument("--maschera-numeri", action="store_true", help="nel report mostra i numeri mascherati (+39…123)")
    return ap


async def main():
    for flusso in (sys.stdout, sys.stderr):
        flusso.reconfigure(errors="replace")
    args = costruisci_parser().parse_args()
    if args.prefisso:
        args.prefisso = "+" + re.sub(r"\D", "", args.prefisso)

    righe, intest = leggi_lista(args.lista, args.col_username, args.col_telefono, args.prefisso)
    if args.limit:
        righe = righe[: args.limit]
    print(f"Righe lette: {len(righe)}")
    api_id, api_hash = leggi_config(args.config)
    client = TelegramClient(args.sessione, api_id, api_hash)
    await client.start()  # al primo avvio: numero, codice ed eventuale 2FA
    try:
        gruppo = await trova_gruppo(client, args.gruppo)
        titolo = getattr(gruppo, "title", "?")
        membri, dichiarati = await scarica_membri(client, gruppo)
        print(f"Gruppo «{titolo}»: {len(membri)} membri (dichiarati: {dichiarati})")
        try:
            await elabora(client, righe, membri, args)
        finally:
            if all("stato" in r for r in righe):
                scrivi_md(args.out, righe, titolo, len(membri), args)
                if args.csv_stati:
                    scrivi_csv_stati(args.csv_stati, righe, intest)
                print(f"\nFatto. Report: {args.out}" + (f" · CSV con stati: {args.csv_stati}" if args.csv_stati else ""))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
