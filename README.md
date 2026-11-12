# invia-sms-a-gruppi
Son stato obbligato ad usare una ai sfortunatamente per generarlo, ma non ero io a gestire il lavoro, ma almeno ne rendo libero il suo risultato così che se altri dovessero averne bisogno possono usarlo.

È un singolo file html, all'inizio era senza javascript, ma c'era da metterci i dati nell'html, in questo modo può esser personalizzato messaggio e lista di contatti a cui mandarlo.

Nell'html spiega come creare i csv

esempio generale:

cognome;nome;telefono;note

Rossi;Mario;3331234567;gruppo A

Bianchi;Anna;+393339876543;



Aggiunti anche altri due script py che inviano gli sms in automatico con Termux, e uno per controllare la lista utenti di un gruppo per capire chi c'è e chi manca.

Se vuoi usare la versione automatica di Termux, god help you.

per controllalista:
serve di python installato: telethon     (e xlrd solo per i file .xls)
config.ini con due righe:   id=123456   /   hash=0123456789abcdef0123456789abcdef (api_id e api_hash si ottengono su https://my.telegram.org -> API development tools)

per l'invio di messaggi con Termux:
Preparazione (una volta):
  1. Installa Termux e Termux:API dalla STESSA fonte (F-Droid o GitHub, non il Play Store).
  2. In Termux:   pkg install python termux-api
  3. Dai il permesso SMS a Termux:API (Impostazioni > App > Termux:API > Permessi). Se e' bloccato:
     Info app > menu (tre puntini) > "Consenti impostazioni con restrizioni", oppure da PC:
         adb shell pm grant com.termux.api android.permission.SEND_SMS
  4. Copia nella stessa cartella lo script e l'elenco CSV. Prima di un invio lungo:  termux-wake-lock

in entrambi i python ci son gli --help, li riporto anche qui ig:



python controlla_lista.py --help
usage: controlla_lista.py [-h] --lista FILE [--col-username NOME]
                          [--col-telefono NOME] [--prefisso +NN]
                          [--limit N] --gruppo LINK [--config FILE]
                          [--sessione NOME] [--telefono] [--max-numeri N]
                          [--cache FILE] [--scadenza-giorni N]
                          [--ricontrolla] [--importa-cache FILE]
                          [--out FILE] [--csv-stati FILE]
                          [--maschera-numeri]

Controlla chi di un elenco (CSV o xls) e' gia' in un gruppo Telegram.
Non modifica mai il gruppo. Scrive un report .md con presenti, da aggiungere e problemi.

FORMATO DELL'ELENCO
  Prima riga = intestazioni. Colonne cercate da sole (anche con altri nomi simili):
    username   (facoltativa se c'e' il telefono)   telefono / cellulare / numero
    nome, cognome (facoltative: servono al report e al confronto per somiglianza di nome)
  Separatore ';' o ','. Il telefono va formattato come testo (Excel rovina i numeri lunghi).

COME FUNZIONA
  1. legge i membri del gruppo (serve esserne membri; se la lista e' nascosta, un admin)
  2. risolve ogni username in un ID e lo confronta con i membri
  3. con --telefono: per le righe non identificate dallo username cerca il numero
  4. ricorda ogni ricerca in una cache: rilanciando non rifa' le richieste

options:
  -h, --help            show this help message and exit

elenco:
  --lista FILE          elenco in CSV o xls (obbligatorio)
  --col-username NOME   forza il nome della colonna username
  --col-telefono NOME   forza il nome della colonna telefono
  --prefisso +NN        prefisso internazionale per i numeri scritti senza
                        «+» (es. +39). Senza, quei numeri vengono scartati
  --limit N             elabora solo le prime N righe (per prova)

telegram:
  --gruppo LINK         link d'invito (t.me/+...) o pubblico del gruppo
  --config FILE         file con id= e hash= (default: config.ini)
  --sessione NOME       nome del file di sessione (default:
                        sessione_controllo)

ricerca per telefono (facoltativa):
  --telefono            cerca per numero le righe che lo username non
                        identifica (vedi avviso privacy)
  --max-numeri N        numeri da cercare al massimo per esecuzione
                        (default: 150)

cache:
  --cache FILE          file di cache (default: cache_telegram.json)
  --scadenza-giorni N   dopo quanti giorni ricontrollare gli esiti
                        negativi (default: 14)
  --ricontrolla         rifa' tutte le ricerche non ancora aggiornate
                        oggi, ignorando la cache
  --importa-cache FILE  aggiunge le voci di un altro file di cache

risultati:
  --out FILE            report (default: controllo_lista.md)
  --csv-stati FILE      scrive anche una copia dell'elenco con le colonne
                        stato, via, telegram_id, note (l'elenco originale
                        non viene mai modificato)
  --maschera-numeri     nel report mostra i numeri mascherati (+39…123)

ESEMPI
  python controlla_lista.py --lista elenco.csv --gruppo 'https://t.me/+XXXXXXXX'
  python controlla_lista.py --lista elenco.csv --gruppo '...' --telefono --prefisso +39
  python controlla_lista.py --lista elenco.xls --gruppo '...' --limit 10     # prova su 10 righe

NOTE
  - Il primo avvio chiede numero, codice (arriva nell'app Telegram) ed eventuale 2FA e crea
    un file di sessione (*.session): equivale a una password, non condividerlo.
  - Telegram limita le ricerche (circa 200 username al giorno): se impone un'attesa lunga lo
    script si ferma, salva la cache e scrive il report parziale. Rilancia piu' tardi.
  - PRIVACY: --telefono importa temporaneamente i numeri tra i contatti per scoprire chi e'
    su Telegram (poi li cancella). Rivela se un numero ha un account: usalo solo su elenchi di
    persone che hanno dato il numero per questo scopo, nel rispetto del GDPR e dei termini di
    Telegram. Elenco e report contengono dati personali: tienili privati.
--------------
❯ python invia_sms.py --help
usage: invia_sms.py [-h] [--lista FILE] [--col-telefono NOME] [--prefisso +NN] [--filtro COLONNA=VALORE] [--messaggio FILE] [--invia] [--test NUMERO] [--max N] [--si] [--pausa-min SEC]
                    [--pausa-max SEC] [--sim SLOT] [--timeout SEC] [--log FILE] [--riprova-incerti] [--no-controllo] [--timeout-api SEC]

Invia un SMS personalizzato a ogni persona di un elenco CSV, da Android con Termux + Termux:API.
Senza --invia fa solo una PROVA (anteprima e conteggi, nessun SMS parte).

FORMATO DELL'ELENCO
  CSV con intestazioni nella prima riga. Colonna obbligatoria: telefono (o cellulare / numero).
  Facoltative: nome, cognome (per {nome} e {cognome} nel testo), riga (identificativo unico).
  Separatore ';' o ','. I numeri senza '+' richiedono --prefisso.

MESSAGGIO
  Testo in messaggio.txt con i segnaposto {nome} e {cognome}. Se il file non esiste lo crea con
  un esempio ed esce. Lo script rifiuta di inviare finche' restano parti tra [ ].

COSA FA PER OGNI SMS
  controlla che Termux:API risponda (se no, prova a risvegliarla; se resta muta si ferma senza
  aver inviato), invia, scrive l'esito nel log e aspetta una pausa casuale. Rilanciando salta
  chi ha gia' esito ok. Un timeout dell'invio e' 'incerto': l'SMS potrebbe essere partito, per
  questo non viene rimandato da solo (--riprova-incerti per rimandarlo).

options:
  -h, --help            show this help message and exit

elenco e messaggio:
  --lista FILE          elenco CSV (obbligatorio, tranne con --test)
  --col-telefono NOME   forza il nome della colonna del telefono
  --prefisso +NN        prefisso internazionale per i numeri scritti senza '+' (es. +39)
  --filtro COLONNA=VALORE
                        usa solo le righe in cui la colonna ha quel valore
  --messaggio FILE      file con il testo (default: messaggio.txt)

invio:
  --invia               invia davvero (senza: solo prova)
  --test NUMERO         invia un solo SMS di prova a questo numero (formato +NN...)
  --max N               invia al massimo N SMS in questa esecuzione
  --si                  non chiedere conferma prima di inviare
  --pausa-min SEC       pausa minima tra due SMS (default: 60)
  --pausa-max SEC       pausa massima tra due SMS (default: 90)
  --sim SLOT            slot SIM da usare sui telefoni dual SIM (prova 0 e 1)
  --timeout SEC         attesa massima per ogni SMS (default: 60)

controlli e log:
  --log FILE            registro degli invii (default: sms_inviati.csv)
  --riprova-incerti     rimanda anche chi ha esito 'incerto' (timeout) nel log
  --no-controllo        non controllare Termux:API prima di ogni SMS
  --timeout-api SEC     attesa del controllo Termux:API (default: 10)

ESEMPI
  python invia_sms.py --lista elenco.csv --prefisso +39           # PROVA: non invia nulla
  python invia_sms.py --test +39XXXXXXXXXX --invia                # un solo SMS al tuo numero
  python invia_sms.py --lista elenco.csv --prefisso +39 --invia --max 3
  python invia_sms.py --lista elenco.csv --prefisso +39 --invia   # tutti (riprende dal log)
  python invia_sms.py --lista elenco.csv --filtro gruppo=A        # solo le righe con gruppo=A

NOTE
  - 'ok' significa che Android ha preso in carico l'SMS, non che sia stato consegnato.
  - Le pause lunghe (default 60-90 s) evitano la richiesta di conferma che Android mostra
    quando un'app manda molti SMS in poco tempo. Controlla che il tuo piano SMS lo permetta.
  - Se compare 'timeout': in Termux prova  termux-toast prova  (se si blocca, Termux:API non e'
    raggiungibile: app installata e aperta una volta, stessa fonte di Termux, ottimizzazione
    batteria disattivata); poi  termux-sms-send -n +NUMERO "prova"  guardando lo schermo per
    eventuali richieste di permesso.
  - Invia solo a persone che si aspettano il messaggio. Elenco e log contengono dati personali.


    
