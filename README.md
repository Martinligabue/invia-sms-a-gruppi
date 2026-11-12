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

in entrambi i python ci son gli --help
