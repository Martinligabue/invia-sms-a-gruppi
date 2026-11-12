#!/usr/bin/env python3
"""
invia_sms.py - invia un SMS personalizzato a ogni persona di un elenco CSV, da un telefono
Android con Termux + Termux:API (nessun root). Gli SMS partono dalla SIM del telefono.

Preparazione (una volta):
  1. Installa Termux e Termux:API dalla STESSA fonte (F-Droid o GitHub, non il Play Store).
  2. In Termux:   pkg install python termux-api
  3. Dai il permesso SMS a Termux:API (Impostazioni > App > Termux:API > Permessi). Se e' bloccato:
     Info app > menu (tre puntini) > "Consenti impostazioni con restrizioni", oppure da PC:
         adb shell pm grant com.termux.api android.permission.SEND_SMS
  4. Copia nella stessa cartella lo script e l'elenco CSV. Prima di un invio lungo:  termux-wake-lock

Aiuto completo:  python invia_sms.py --help
"""
import argparse
import csv
import math
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

GSM = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
       "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM_EXT = "^{}\\[~]|€"
TEL_RE = re.compile(r"^\+\d{8,15}$")
SCI = re.compile(r"^\d+([.,]\d+)?[eE][+-]?\d+$")
ESEMPIO = "Ciao {nome}, [scrivi qui il tuo messaggio]\n"


# ------------------------------------------------------------------ input ---
def leggi_testo(path):
    dati = Path(path).read_bytes()
    try:
        return dati.decode("utf-8-sig")
    except UnicodeDecodeError:
        return dati.decode("cp1252")


def trova_col(head, esplicita, chiavi, esatto=False):
    if esplicita:
        if esplicita.strip().lower() in head:
            return head.index(esplicita.strip().lower())
        sys.exit(f"Colonna '{esplicita}' non trovata. Intestazioni: {head}")
    for k in chiavi:
        for i, h in enumerate(head):
            if (h == k) if esatto else (k in h):
                return i
    return None


def numero(raw, prefisso):
    """Numero in formato +..., oppure '' se non utilizzabile."""
    s = re.sub(r"[^\d+]", "", raw or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if s and not s.startswith("+") and prefisso:
        s = prefisso + s
    return s if TEL_RE.match(s) else ""


def leggi_elenco(path, col_tel, filtro, prefisso):
    testo = leggi_testo(path)
    righe = testo.splitlines()
    if not righe:
        return []
    delim = ";" if righe[0].count(";") >= righe[0].count(",") else ","
    tutte = list(csv.reader(righe, delimiter=delim))
    head = [h.strip().lower() for h in tutte[0]]
    it = trova_col(head, col_tel, ["cellulare", "telefono", "numero", "tel"])
    if it is None:
        sys.exit(f"Non trovo la colonna del telefono (vedi --col-telefono). Intestazioni: {head}")
    ic = trova_col(head, None, ["cognome"], esatto=True)
    inm = trova_col(head, None, ["nome"], esatto=True)
    ir = trova_col(head, None, ["riga"], esatto=True)
    fcol = fval = None
    if filtro:
        if "=" not in filtro:
            sys.exit("--filtro va scritto COLONNA=VALORE (es. gruppo=A)")
        fcol, fval = (x.strip() for x in filtro.split("=", 1))
        fcol = fcol.lower()
        if fcol not in head:
            sys.exit(f"--filtro: colonna '{fcol}' non trovata. Intestazioni: {head}")
    get = lambda r, i: r[i].strip() if i is not None and i < len(r) else ""
    out = []
    for n, r in enumerate(tutte[1:], start=2):
        if not any(c.strip() for c in r):
            continue
        if fcol and get(r, head.index(fcol)) != fval:
            continue
        out.append({"riga": get(r, ir) or str(n), "cognome": get(r, ic), "nome": get(r, inm),
                    "raw": get(r, it), "telefono": numero(get(r, it), prefisso)})
    return out


# ---------------------------------------------------------------- messaggio ---
def segmenti(testo):
    """(unita', numero di SMS, codifica)"""
    if all(c in GSM or c in GSM_EXT for c in testo):
        n = sum(2 if c in GSM_EXT else 1 for c in testo)
        return n, (1 if n <= 160 else math.ceil(n / 153)), "GSM-7"
    n = len(testo.encode("utf-16-le")) // 2
    return n, (1 if n <= 70 else math.ceil(n / 67)), "UCS-2 (ci sono caratteri speciali o emoji: costa di piu')"


def componi(modello, nome, cognome=""):
    t = modello.replace("{nome}", nome.title() if nome else "").replace("{cognome}", cognome.title() if cognome else "")
    return re.sub(r"\s+,", ",", re.sub(r"[ \t]{2,}", " ", t)).strip()


# --------------------------------------------------------------------- invio ---
def invia(numero_, testo, sim, timeout=60):
    cmd = ["termux-sms-send", "-n", numero_]
    if sim is not None:
        cmd += ["-s", str(sim)]
    cmd.append(testo)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        sys.exit("termux-sms-send non trovato: pkg install termux-api e installa l'app Termux:API.")
    except subprocess.TimeoutExpired:
        return False, "timeout"
    if r.returncode != 0:
        return False, ((r.stderr or r.stdout).strip()[:200] or f"codice {r.returncode}")
    return True, ""


def api_viva(timeout):
    """True se Termux:API risponde (comando innocuo, senza effetti)."""
    try:
        r = subprocess.run(["termux-battery-status"], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        sys.exit("termux-battery-status non trovato: pkg install termux-api e installa l'app Termux:API.")
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0 and "percentage" in r.stdout


def risveglia():
    """Apre l'app Termux:API e torna a Termux: la fa uscire dallo stato in cui Android la sospende."""
    for pkg in ("com.termux.api", "com.termux"):
        try:
            subprocess.run(["am", "start", "-a", "android.intent.action.MAIN", "-c",
                            "android.intent.category.LAUNCHER", "-p", pkg],
                           capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(3)


def assicura_api(timeout):
    for _ in range(3):
        if api_viva(timeout):
            return True
        print("   Termux:API non risponde: provo a risvegliarla...")
        risveglia()
    return api_viva(timeout)


def leggi_log(path, riprova_incerti=False):
    """Numeri da non rimandare: esito ok e, salvo --riprova-incerti, esito incerto (timeout)."""
    p = Path(path)
    if not p.exists():
        return set()
    saltare = {"ok"} if riprova_incerti else {"ok", "incerto"}
    with open(p, newline="", encoding="utf-8-sig") as f:
        return {r["telefono"] for r in csv.DictReader(f, delimiter=";") if r.get("esito") in saltare}


def scrivi_log(path, r, esito, dettaglio):
    nuovo = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8-sig" if nuovo else "utf-8") as f:
        w = csv.writer(f, delimiter=";")
        if nuovo:
            w.writerow(["quando", "riga", "cognome", "nome", "telefono", "esito", "dettaglio"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), r["riga"], r["cognome"],
                    r["nome"], r["telefono"], esito, dettaglio])


def costruisci_parser():
    ap = argparse.ArgumentParser(
        prog="invia_sms.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Invia un SMS personalizzato a ogni persona di un elenco CSV, da Android con Termux + Termux:API.\n"
            "Senza --invia fa solo una PROVA (anteprima e conteggi, nessun SMS parte).\n\n"
            "FORMATO DELL'ELENCO\n"
            "  CSV con intestazioni nella prima riga. Colonna obbligatoria: telefono (o cellulare / numero).\n"
            "  Facoltative: nome, cognome (per {nome} e {cognome} nel testo), riga (identificativo unico).\n"
            "  Separatore ';' o ','. I numeri senza '+' richiedono --prefisso.\n\n"
            "MESSAGGIO\n"
            "  Testo in messaggio.txt con i segnaposto {nome} e {cognome}. Se il file non esiste lo crea con\n"
            "  un esempio ed esce. Lo script rifiuta di inviare finche' restano parti tra [ ].\n\n"
            "COSA FA PER OGNI SMS\n"
            "  controlla che Termux:API risponda (se no, prova a risvegliarla; se resta muta si ferma senza\n"
            "  aver inviato), invia, scrive l'esito nel log e aspetta una pausa casuale. Rilanciando salta\n"
            "  chi ha gia' esito ok. Un timeout dell'invio e' 'incerto': l'SMS potrebbe essere partito, per\n"
            "  questo non viene rimandato da solo (--riprova-incerti per rimandarlo)."),
        epilog=(
            "ESEMPI\n"
            "  python invia_sms.py --lista elenco.csv --prefisso +39           # PROVA: non invia nulla\n"
            "  python invia_sms.py --test +39XXXXXXXXXX --invia                # un solo SMS al tuo numero\n"
            "  python invia_sms.py --lista elenco.csv --prefisso +39 --invia --max 3\n"
            "  python invia_sms.py --lista elenco.csv --prefisso +39 --invia   # tutti (riprende dal log)\n"
            "  python invia_sms.py --lista elenco.csv --filtro gruppo=A        # solo le righe con gruppo=A\n\n"
            "NOTE\n"
            "  - 'ok' significa che Android ha preso in carico l'SMS, non che sia stato consegnato.\n"
            "  - Le pause lunghe (default 60-90 s) evitano la richiesta di conferma che Android mostra\n"
            "    quando un'app manda molti SMS in poco tempo. Controlla che il tuo piano SMS lo permetta.\n"
            "  - Se compare 'timeout': in Termux prova  termux-toast prova  (se si blocca, Termux:API non e'\n"
            "    raggiungibile: app installata e aperta una volta, stessa fonte di Termux, ottimizzazione\n"
            "    batteria disattivata); poi  termux-sms-send -n +NUMERO \"prova\"  guardando lo schermo per\n"
            "    eventuali richieste di permesso.\n"
            "  - Invia solo a persone che si aspettano il messaggio. Elenco e log contengono dati personali."))
    g = ap.add_argument_group("elenco e messaggio")
    g.add_argument("--lista", metavar="FILE", help="elenco CSV (obbligatorio, tranne con --test)")
    g.add_argument("--col-telefono", metavar="NOME", help="forza il nome della colonna del telefono")
    g.add_argument("--prefisso", metavar="+NN", help="prefisso internazionale per i numeri scritti senza '+' (es. +39)")
    g.add_argument("--filtro", metavar="COLONNA=VALORE", help="usa solo le righe in cui la colonna ha quel valore")
    g.add_argument("--messaggio", default="messaggio.txt", metavar="FILE",
                   help="file con il testo (default: messaggio.txt)")
    g = ap.add_argument_group("invio")
    g.add_argument("--invia", action="store_true", help="invia davvero (senza: solo prova)")
    g.add_argument("--test", metavar="NUMERO", help="invia un solo SMS di prova a questo numero (formato +NN...)")
    g.add_argument("--max", type=int, metavar="N", help="invia al massimo N SMS in questa esecuzione")
    g.add_argument("--si", action="store_true", help="non chiedere conferma prima di inviare")
    g.add_argument("--pausa-min", type=float, default=60.0, metavar="SEC", help="pausa minima tra due SMS (default: 60)")
    g.add_argument("--pausa-max", type=float, default=90.0, metavar="SEC", help="pausa massima tra due SMS (default: 90)")
    g.add_argument("--sim", type=int, metavar="SLOT", help="slot SIM da usare sui telefoni dual SIM (prova 0 e 1)")
    g.add_argument("--timeout", type=int, default=60, metavar="SEC", help="attesa massima per ogni SMS (default: 60)")
    g = ap.add_argument_group("controlli e log")
    g.add_argument("--log", default="sms_inviati.csv", metavar="FILE", help="registro degli invii (default: sms_inviati.csv)")
    g.add_argument("--riprova-incerti", action="store_true", help="rimanda anche chi ha esito 'incerto' (timeout) nel log")
    g.add_argument("--no-controllo", action="store_true", help="non controllare Termux:API prima di ogni SMS")
    g.add_argument("--timeout-api", type=int, default=10, metavar="SEC", help="attesa del controllo Termux:API (default: 10)")
    return ap


def main():
    args = costruisci_parser().parse_args()
    if args.prefisso:
        args.prefisso = "+" + re.sub(r"\D", "", args.prefisso)

    pm = Path(args.messaggio)
    if not pm.exists():
        pm.write_text(ESEMPIO, encoding="utf-8")
        sys.exit(f"Ho creato {args.messaggio} con un testo di esempio: modificalo "
                 "(togli le parti tra [ ]) e rilancia.")
    modello = pm.read_text(encoding="utf-8").strip()
    if "[" in modello or "]" in modello:
        sys.exit(f"In {args.messaggio} ci sono ancora parti tra [ ]: completa il testo.")

    if args.test:
        if not TEL_RE.match(args.test):
            sys.exit("Il numero di prova deve essere nel formato +NNXXXXXXXXXX")
        dest = [{"riga": "test", "cognome": "", "nome": "Test", "raw": args.test, "telefono": args.test}]
        saltati_log = set()
    else:
        if not args.lista or not Path(args.lista).exists():
            sys.exit("Indica l'elenco con --lista (vedi --help)")
        dest = leggi_elenco(args.lista, args.col_telefono, args.filtro, args.prefisso)
        saltati_log = leggi_log(args.log, args.riprova_incerti)

    visti, validi, non_validi, doppi, gia = set(), [], 0, 0, 0
    for r in dest:
        t = r["telefono"]
        if not t:
            non_validi += 1
        elif t in saltati_log:
            gia += 1
        elif t in visti:
            doppi += 1
        else:
            visti.add(t)
            validi.append(r)
    if args.max:
        validi = validi[: args.max]

    print(f"Destinatari letti: {len(dest)} · da inviare ora: {len(validi)} · "
          f"gia' inviati (log): {gia} · numeri doppi: {doppi} · numeri non validi: {non_validi}")
    if non_validi and not args.prefisso and not args.test:
        print("(Se i numeri non hanno il '+', indica il prefisso con --prefisso, es. --prefisso +39)")
    if not validi:
        return
    esempio = componi(modello, validi[0]["nome"], validi[0]["cognome"])
    n, seg, cod = segmenti(esempio)
    print(f"\nAnteprima (primo destinatario):\n  «{esempio}»\n  {n} caratteri, {seg} SMS per destinatario, {cod}")
    durata = len(validi) * (args.pausa_min + args.pausa_max) / 2 / 60
    print(f"Durata stimata con le pause: circa {durata:.0f} minuti.")

    if not args.invia:
        print("\nPROVA: non ho inviato nulla. Aggiungi --invia per inviare davvero.")
        return
    if not args.si and input(f"\nInvio {len(validi)} SMS. Procedo? [s/N] ").strip().lower() != "s":
        sys.exit("Annullato.")

    ok = errori = incerti = consecutivi = 0
    try:
        for i, r in enumerate(validi, 1):
            testo = componi(modello, r["nome"], r["cognome"])
            etichetta = f"{r['cognome']} {r['nome']}".strip() or "(test)"
            if not args.no_controllo and not assicura_api(args.timeout_api):
                print(f"\nTermux:API non risponde nemmeno dopo il risveglio: mi fermo SENZA aver inviato a "
                      f"{etichetta}. Sistema (apri l'app Termux:API, controlla batteria e permessi) e rilancia "
                      "lo stesso comando: riparte da qui.")
                break
            esito, det = invia(r["telefono"], testo, args.sim, args.timeout)
            if esito:
                ok += 1
                consecutivi = 0
                stato, msg = "ok", "ok"
            elif det == "timeout":
                incerti += 1
                consecutivi += 1
                stato, msg = "incerto", "NESSUNA RISPOSTA (timeout): non so se e' partito"
            else:
                errori += 1
                consecutivi += 1
                stato, msg = "errore", "ERRORE: " + det
            print(f"[{i}/{len(validi)}] {etichetta} -> {msg}")
            if not args.test:
                scrivi_log(args.log, r, stato, det)
            if consecutivi >= 3:
                print("\n3 problemi di fila: mi fermo. Controlla Termux:API, il permesso SMS e la SIM (vedi --help).")
                break
            if i < len(validi):
                time.sleep(random.uniform(args.pausa_min, args.pausa_max))
    except KeyboardInterrupt:
        print("\nInterrotto: rilancia lo stesso comando per riprendere.")
    print(f"\nFatto: {ok} ok, {errori} errori, {incerti} incerti (timeout)."
          + ("" if args.test else f" Log: {args.log}"))
    if incerti:
        print("Gli 'incerti' potrebbero essere partiti: controlla gli SMS inviati sul telefono.")


if __name__ == "__main__":
    main()
