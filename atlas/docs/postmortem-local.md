# Postmortem — pierderea workerului primar în simularea locală

**Tip:** experiment controlat local, executat; nu incident AWS.  
**Impact:** 20.000 comenzi confirmate; zero pierderi; zero efecte suplimentare.  
**Dovezi:** `results/local-simulation.json`, testele unitare.  
**Mediu:** Python 3.12, SQLite WAL, o singură DB; două regiuni de workeri emulate.

## Desfășurare

1. Se acceptă 20.000 comenzi sintetice în batch-uri de maximum 100. Fiecare commit include outbox și două destinații de livrare.
2. Workerul primar procesează 20% dintre evenimente. Ulterior se simulează indisponibilitatea lui și a cozii primare.
3. Workerul secundar revendică independent evenimentele din outbox și procesează toate identitățile.
4. Se relivrează 10% dintre evenimente și se replay-uiesc 1.000 identități existente.
5. Un eveniment cu versiune incompatibilă eșuează de cinci ori și este mutat în lista DLQ a simulării.
6. Se creează și se redeschide un backup SQLite. Identitățile ledger-ului sunt comparate cu toate receipts inițiale.

## Rezultat

20.000 identități acceptate = 20.000 identități ledger = 20.000 identități după restore. Sumele au fost conservate. Au fost suprimate 7.000 livrări duplicate: 4.000 procesate înainte de fault, 2.000 redelivery și 1.000 replay.

Timpii și ratele locale exacte sunt în JSON, pentru a evita copierea unor valori care se schimbă la rerulare. Nu s-au măsurat RTO/RPO AWS. DLQ-ul local nu validează temporizarea sau redrive-ul serviciului SQS.

## Cauza și mecanismul de recuperare

Fault-ul a fost pierderea intenționată a workerului primar, după aplicarea parțială a efectelor. Outbox-ul a rămas disponibil. Livrările către regiunea secundară au propria stare și nu depind de ACK-urile regiunii primare. Inserarea inbox + ledger + status atomică a împiedicat repetarea efectelor.

## Ce mai trebuie verificat

| Acțiune | Criteriu | Stare |
|---|---|---|
| PostgreSQL CI | CTE atomic, locking concurent și rollback | Pregătit; neexecutat aici |
| Terraform validate/plan | Provider schema și plan în contul lab | Pregătit; neexecutat aici |
| Load AWS | ≥500–1.000 efecte unice/s susținute | Nemăsurat |
| Failover API și DB | RTO<300s, zero IDs confirmate lipsă în testul controlat | Nemăsurat |
| RPO | metrici și receipts înainte de replay | Nemăsurat |
| Backup/restore AWS | Snapshot și copie disponibile, audit restore | Nemăsurat |

Nu se extrapolează această simulare la o avarie fizică AWS sau la split-brain sub partiții de rețea.
