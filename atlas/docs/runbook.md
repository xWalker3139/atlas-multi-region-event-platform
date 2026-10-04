# Atlas runbook

## Triage: primele 5 minute

1. Înregistrează incidentul în UTC; păstrează receipts și eroarea clientului. Verifică `/live` și `/health` pe **ambele URL-uri regionale**, nu numai domeniul global.
2. Verifică Route53 health checks, Lambda Errors/Throttles, SQS age/DLQ, conexiuni și CPU DB, `AuroraGlobalDBRPOLag` și logul supervisorului din Frankfurt.
3. Verifică topologia globală:

```bash
aws rds describe-global-clusters --region eu-central-1 --global-cluster-identifier atlas-global
aws logs tail /aws/lambda/atlas-failover --region eu-central-1 --since 10m
```

4. Dacă secondary `/health` este healthy, DB este accesibil și nu este necesară promovarea. Verifică failover-ul API și cache-ul DNS al clientului. Clientul reîncearcă aceeași comandă cu aceeași cheie.
5. Dacă ambele `/health` eșuează, dar secondary `/live` răspunde, supervisorul ar trebui să numere trei observații. Verifică `suspect`, `promote`, cooldown și starea RDS. Nu invoca în paralel o a doua promovare.

Timeout HTTP nu confirmă că tranzacția a eșuat. Nu schimba idempotency key la retry. Pentru un batch cu conflict 409, identifică și corectează cheia; nu retrimite în buclă același batch invalid.

## Supervisor indisponibil sau promovare blocată

Verifică IAM `rds:FailoverGlobalCluster`, versiunea comună a engine-ului, starea clusterului secundar și existența unei instanțe în secundară. Toate cele patru instanțe sunt declarate în Terraform. `transition_or_cooldown` este normal în timpul unei operații.

Dacă e o partiție de rețea și sursa este încă accesibilă, înainte de promovarea manuală oprește API-ul și publisherii sursă, așteaptă terminarea invocărilor curente și confirmă noul writer. AWS documentează riscul de split-brain. Nu promova numai fiindcă un singur canary eșuează.

Promovare manuală de urgență, numai după stabilirea faptului că failover-ul global este necesar:

```bash
TARGET_ARN=$(terraform -chdir=infra output -raw secondary_cluster_arn)
aws rds failover-global-cluster --region eu-central-1 \
  --global-cluster-identifier atlas-global --target-db-cluster-identifier "$TARGET_ARN" --allow-data-loss
```

`--allow-data-loss` este un compromis real: ultimele commit-uri nereplicate se pot pierde. Salvează metricile și receipts înainte de replay/resubmit pentru a putea măsura pierderea inițială.

## După recuperare

Confirmă writerul nou din `GlobalClusterMembers`, readiness și un commit nou. Rulează reconcilierea pe toate receipts confirmate. Lasă queue-urile să se golească și verifică statusurile și sumele, nu doar disponibilitatea HTTP.

```bash
python tools/reconcile.py --receipts results/YOUR-RUN/receipts.jsonl \
  --function atlas-secondary-migrate --output results/YOUR-RUN-audit.json
```

Nu executa failback automat. Înainte de revenire, confirmă că vechiul cluster a reintrat ca secundar și s-a sincronizat, ambele servicii sunt healthy, backup-urile sunt disponibile și nu există o promovare în curs. Folosește switchover planificat, care sincronizează datele:

```bash
PRIMARY_ARN=$(terraform -chdir=infra output -raw primary_cluster_arn)
aws rds switchover-global-cluster --region eu-central-1 \
  --global-cluster-identifier atlas-global --target-db-cluster-identifier "$PRIMARY_ARN"
```

Verifică compatibilitatea engine-ului și API-ului înainte de execuție. După orice promovare, rulează `terraform plan -refresh-only`, inspectează schimbările și apoi un plan normal. Nu aplica un plan care recreează DB. Alarma și widgetul RPO configurate pentru `atlas-secondary` trebuie adaptate regiunii care este acum secundară.

## DLQ și backlog

Un mesaj invalid, versiune necunoscută sau payload diferit de outbox eșuează. Consumerul raportează numai mesajele nereușite; cele reușite se șterg. Visibility timeout 180s este mai mare decât de șase ori timeout-ul consumerului de 25s. După 5 receive-uri mesajul ajunge în DLQ; retenția este 14 zile.

Pentru backlog verifică: concurrency/quota, DB connections/CPU, latenta WAN, publisher Errors și lease expiry. EventBridge programează 16 shard-uri pe minut; latența inițială a publicării poate ajunge la aproximativ un minut. Dacă publisherul eșuează după enqueue, așteaptă expirarea lease-ului de 120s; duplicatele sunt sigure. Niciun rând nu este marcat `sent_at` pentru intrările eșuate ale unui `SendMessageBatch`.

Inspectează un eșantion DLQ, identifică eroarea și repară cauza. Nu șterge inbox-ul pentru a permite retry. Redrive după remediere:

```bash
aws sqs start-message-move-task --region eu-central-1 \
  --source-arn arn:aws:sqs:eu-central-1:YOUR-ACCOUNT:atlas-secondary-dlq \
  --max-number-of-messages-per-second 100
```

Callerul are nevoie de permisiunile SQS de redrive. Payload-ul invalid intenționat din chaos nu se redrive până când există un consumator compatibil sau o corecție autorizată. Nu modifica identitatea unui eveniment existent pentru a ascunde o eroare.

## Backup și restore izolat

Politica: retenție Aurora PITR 14 zile; AWS Backup zilnic, 30 zile, cu snapshot copy în cealaltă regiune. Ambele clustere sunt selectate, astfel încât planurile rămân relevante după schimbarea writerului. Verifică rezultatul joburilor; existența unui plan nu demonstrează existența unui backup valid. Copiile de snapshot nu oferă automat PITR între regiuni.

Snapshot manual și copie criptată:

```bash
python tools/dr.py snapshot --cluster atlas-primary --region eu-west-1 \
  --copy-region eu-central-1 --copy-kms-key YOUR-DESTINATION-KMS-KEY-ARN
```

După failover selectează clusterul care este writer; nu presupune că `atlas-primary` este încă writer. Pentru restore alege snapshotul și infrastructura **isolată de test**:

```bash
python tools/dr.py restore --region eu-central-1 \
  --identifier atlas-restore-YYYYMMDD --snapshot YOUR-SNAPSHOT-ARN \
  --subnet-group YOUR-RESTORE-SUBNET-GROUP --security-group YOUR-RESTORE-DB-SG
# Inspectează planul afișat; adaugă --execute pentru restaurarea efectivă.
```

Scriptul creează un cluster nou și o instanță private; nu înlocuiește DB live. Confirmă `available`, acces SQL, counts, sume, FK/chei unice și reprocess fără creșterea ledger-ului. Pentru verificare pe snapshot folosește funcția Lambda izolată creată de scriptul de mai jos; nu schimba configurația consumerilor live:

```bash
python tools/verify_restore.py --db-host RESTORED-CLUSTER-ENDPOINT \
  --receipts results/YOUR-RUN/receipts.jsonl --archive-bucket YOUR-ARCHIVE-BUCKET \
  --source-function atlas-secondary-migrate --package dist/atlas-lambda.zip \
  --max-events 1000 --output results/restore-audit.json --execute
```

Înainte de execuție, rulează aceeași comandă fără `--execute`. Funcția temporară folosește VPC-ul și secretul funcției sursă; grupul DB restaurat trebuie să permită accesul acelui VPC. Un snapshot păstrează parola de la momentul său; dacă parola s-a rotit, pregătește secretul potrivit pentru restore. Scriptul declară dacă verificarea este un eșantion și elimină funcția temporară la final. Pentru toate evenimentele, `--max-events 0`.

## Replay și rehidratare

Pentru DB existent, replay trimite aceeași identitate în SQS. Inbox-ul suprimă efectele deja aplicate:

```bash
python tools/replay.py --region eu-central-1 --receipts results/YOUR-RUN/receipts.jsonl \
  --bucket YOUR-ARCHIVE-BUCKET --queue YOUR-SQS-URL --execute
```

După restore, un eveniment ulterior snapshotului nu are încă `orders`/`outbox`. Rehidratarea explicită reconstruiește aggregate-ul din arhivă, validează identitatea deterministă și conținutul, apoi îl poate procesa. `verify_restore.py` face acest lucru exclusiv pe DB de test. Dacă pregătești ulterior o recuperare live, folosește `--recover-function` numai pentru funcția de administrare asociată bazei de date care va fi procesată de queue-ul ales. Nu trimite la o coadă live evenimente recuperate într-o bază de test.

Nu există garanție că arhiva conține fiecare commit imediat după acceptare. Un fișier lipsă este raportat ca eroare. Manifestul de intenții al clientului permite reluarea comenzilor care nu există nici în replica promovată, nici în arhivă. Păstrează verdictul de pierdere inițială înainte de această reluare.

## Experiment întrerupt

`chaos/experiment.py` scrie rollback înainte de mutație și încearcă restaurarea în `finally`. Dacă procesul este omorât sau AWS nu răspunde la rollback:

```bash
python chaos/experiment.py --restore results/YOUR-CHAOS/rollback.json
```

Verifică restaurarea explicit în AWS. Un raport `rollback_complete=false` înseamnă că laboratorul poate avea fault-ul încă activ.

## Cleanup

Oprește experimentele și generatorul; salvează rapoartele și copiile necesare. Dezactivează supervisorul înainte de demontarea infrastructurii. Elimină separat clusterele restaurate, cu snapshot final dacă este necesar. Păstrează state-ul și cheile KMS atât timp cât există backup-uri care depind de ele.

Revizuiește un plan `terraform destroy`; dezactivează `deletion_protection` explicit în laborator. Bucket-urile nu au `force_destroy`; arhivele și versiunile lor trebuie administrate conștient. Snapshoturile finale folosesc nume deterministe; la recrearea unui laborator cu același nume verifică să nu existe deja acel snapshot. Resource-urile AWS Backup nu sunt demontate până când recovery points necesare au fost păstrate sau eliminate explicit.
