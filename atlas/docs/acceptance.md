# Protocol de acceptanță AWS

## Condiții inițiale

Cont dedicat laboratorului, tag `Project=atlas`, ambele clustere disponibile, aceeași versiune engine, schema migrată, API-urile regionale și domeniul global healthy. `ATLAS_TOKEN` este exportat. Sincronizează ceasul generatorului. Păstrează rapoartele originale și toate receipts; nu suprascrie un experiment anterior.

## 1. Throughput normal

Rulează separat 500 și 1.000 evenimente **unice**/s, timp de 10 minute fiecare. Endpoint-ul batch reduce numărul request-urilor HTTP; 10 request-uri/s ×100 comenzi =1.000 evenimente/s.

```bash
python tools/load.py --url "$ATLAS_URL" --rate 500 --duration 600 --output results/load-500
python tools/reconcile.py --receipts results/load-500/receipts.jsonl \
  --function atlas-secondary-migrate --min-rate 500 --output results/audit-500.json

python tools/load.py --url "$ATLAS_URL" --rate 1000 --duration 600 --output results/load-1000
python tools/reconcile.py --receipts results/load-1000/receipts.jsonl \
  --function atlas-secondary-migrate --min-rate 1000 --output results/audit-1000.json
```

Acceptarea este evaluată separat de procesare. `summary.json` verifică rata acceptării, programarea open-loop, retry și comenzi necunoscute/respinse. `reconciliation.json` verifică toate event IDs confirmate, sumele și statusul `COMPLETED`, și calculează rata efectelor unice între primul și ultimul `processed_at`. Verifică manual `processing_rate_pass`; codul de ieșire al reconcilerului testează pierderea/corectitudinea, nu performanța. Completează evaluarea cu graficele SQS age/backlog. Intervalele unui batch nu demonstrează singure un throughput susținut; raportul trebuie să includă durata și lipsa unei creșteri continue a backlog-ului.

Profilul k6 adaugă request-uri duplicate la 10% dintre iterații:

```bash
RUN_ID="k6-$(date +%s)" EVENT_RATE=1000 k6 run --summary-export results/k6-summary.json load/atlas.js
```

k6 verifică identitățile și răspunsurile API. Pentru verificarea de zero pierderi, folosește generatorul Python, care salvează intenții și receipts.

## 2. API failover automat

Într-un terminal rulează load test timp de 15 minute. În altul, după două minute, pornește:

```bash
python chaos/experiment.py --mode api-outage --url "$ATLAS_URL" \
  --duration 360 --output results/api-chaos --execute
```

Scriptul setează concurrency=0 numai pe Lambda API primară și îl restabilește la final. Route53 trebuie să trimită request-urile în Frankfurt. DB rămâne în Irlanda: acesta testează failover-ul aplicației, nu al bazei de date. Folosește ID-urile explicite dacă ai modificat numele proiectului. RTO se măsoară de la injectare până la începutul primului interval de scrieri reușite stabil ≥30s; se invalidează dacă apare din nou un eșec.

## 3. DB failover automat, test controlat zero-loss

Nu opri API secundară sau supervisorul. Confirmă un lag RPO mic înainte de experiment și arhivează metricile. Pornește load test de 15 minute; injectează izolarea accesului SQL în primară:

```bash
PRIMARY_SG=$(terraform -chdir=infra output -raw primary_database_sg)
python chaos/experiment.py --mode db-isolation --security-group "$PRIMARY_SG" \
  --url "$ATLAS_URL" --duration 360 --output results/db-chaos --execute
```

Supervisorul trebuie să observe trei eșecuri, să promoveze clusterul din Frankfurt și să înceteze repetarea. Scriptul nu invocă direct promovarea: astfel testul verifică automatizarea. Replicarea storage rămâne funcțională în această injecție; compară starea și lag-ul exact din momentul failover-ului.

După finalizare rulează reconcilierea pe manifestul complet. Pragurile: zero event IDs confirmate lipsă; zero sume greșite; zero comenzi incomplete după drain; RTO<300s. Păstrează numărul de request-uri respinse și retry-urile; request-urile care nu au fost confirmate nu se includ automat în RPO.

Un test zero-loss numai pe count total poate ascunde o comandă lipsă și una suplimentară. Reconcilerul verifică identitățile și sumele, nu doar counts.

## 4. RPO apropiat de zero

Exportă seria `AuroraGlobalDBRPOLag` din regiunea secundară, statistic `Maximum`, împreună cu timestampul injectării. Ținta: ≤1000ms în intervalul de referință. CloudWatch la 60s nu oferă o măsurătoare per commit și nu exclude un spike între puncte; pentru un raport strict capturează și funcția Aurora `aurora_global_db_status()` înainte de injecție și păstrează LSN/timestamps conform documentației engine-ului.

Raportează: lag observat, event IDs confirmate dar absente imediat după recuperare, timestamps ale receipts și orice replay/resubmit ulterior. Dacă probele lipsesc, RPO rămâne `not measured`. Dacă găsești pierderi, rezultatul este FAIL chiar dacă replay-ul le repară ulterior. Un test switchover planificat nu înlocuiește un test de failover forțat.

## 5. Crash / duplicate / poison / replay

Local, `make test` verifică crash după publish, lease retry, conflict concurent, efect rollback și ACK pierdut. Pentru SQS real, trimite un payload invalid în coada de laborator, verifică retry și DLQ după 5 receive-uri. Nu modifica evenimentul original la replay.

```bash
SECONDARY_QUEUE=$(terraform -chdir=infra output -raw secondary_queue)
SECONDARY_BUCKET=$(terraform -chdir=infra output -raw secondary_archive)
python tools/replay.py --receipts results/load-500/receipts.jsonl \
  --bucket "$SECONDARY_BUCKET" --queue "$SECONDARY_QUEUE"
# Revizuiește selecția, apoi rulează aceeași comandă cu --execute.
```

După replay, aceeași reconciliere trebuie să treacă. Counts și totalul monetar nu trebuie să crească pentru evenimente deja procesate. Absența arhivei unui eveniment este o eroare și nu este ignorată de script.

## 6. Backup și restore

Verifică minimum un backup AWS Backup `COMPLETED` și un copy job `COMPLETED`, plus o restaurare izolată după procedura din runbook. Restore-ul trebuie să păstreze identitățile și deduplication state. Pentru evenimente ulterioare snapshotului, folosește rehidratarea operatorului și replay. Nu schimba DB_HOST al sistemului activ pentru un test de restore.

## Dovezi de păstrat

`intentions.jsonl`, `receipts.jsonl`, `summary.json`, audit pe fiecare run, `experiment.json`, rollback și confirmarea restaurării fault-ului, logurile supervisorului, RDS events, grafice/CSV CloudWatch, rezultatul backup/copy/restore și planul Terraform folosit. Completează postmortem-ul cu timpul UTC al fiecărui eveniment și valoarea măsurată a fiecărui prag.
