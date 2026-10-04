# ADR 001 — două regiuni, writer unic și procesare at-least-once

**Status:** implementat pentru laborator; validarea AWS rămâne de executat.

## Alegerea

Aurora PostgreSQL Global Database în Irlanda și Frankfurt; câte două instanțe pe regiune, în subnet-uri din două AZ. Lambda și SQS în ambele regiuni. Nu există o a treia regiune. Aplicațiile folosesc global writer endpoint prin peering, nu write forwarding: outbox-ul are nevoie de locking `FOR UPDATE SKIP LOCKED`.

O tranzacție acceptă comanda și inserează evenimentul și înregistrările de livrare. SQS Standard livrează at-least-once. Cheia unică a inbox-ului, inserarea ledger-ului și statusul comenzii formează o operație SQL atomică. În backendul PostgreSQL, un CTE cu operații DML reduce călătoriile între regiuni și păstrează atomicitatea. SQLite folosește tranzacții explicite și este exclusiv backend de test.

Nu folosim tokenuri de deduplicare cu expirare pentru corectitudinea registrului. Inbox-ul și outbox-ul se păstrează. Orice politică viitoare de retenție trebuie să garanteze că nu se poate reintroduce un efect pentru o identitate a cărei deduplicare a fost ștearsă.

## De ce nu active-active pe DynamoDB MREC

Replicarea last-writer-wins poate introduce conflicte pe idempotency și outbox. Tranzacțiile sunt atomice numai în regiunea sursă și nu se replică atomic. DynamoDB MRSC necesită trei regiuni, cu trei replici sau două replici și witness; nu oferă `TransactWriteItems`. Aurora DSQL multi-region are, de asemenea, un witness. Cerința acestei variante este exact două regiuni.

## Ce garantează codul

- Un 202 se emite după commit. Un răspuns pierdut se rezolvă cu același idempotency key.
- Un conflict de conținut nu creează altă comandă.
- Crash după enqueue și înainte de `sent_at` poate relivra, fără efect duplicat.
- Crash după efect și înainte de ACK SQS poate relivra, fără efect duplicat.
- Evenimentul modificat sau necunoscut nu este aplicat; este retried și ajunge în DLQ.
- Coada primară nu este necesară pentru publisherul și consumatorul secundar.
- Identitatea evenimentului rămâne aceeași la replay și după rehidratarea autorizată din arhivă.

Aceste proprietăți privesc registrul SQL intern. Un procesator extern de plăți trebuie să ofere propria cheie idempotentă și reconciliere; un inbox local nu poate garanta singur exactly-once peste un apel HTTP extern.

## Failover și partiții de rețea

Route 53 verifică readiness la 30 secunde, după trei eșecuri. Supervisorul rulează în Frankfurt la fiecare minut. Dacă una dintre API-uri poate accesa writerul, nu promovează DB. Dacă ambele nu pot accesa writerul, dar serviciul secundar `/live` funcționează, numără trei observații și invocă managed failover. O tranziție sau cooldown de 600 secunde blochează repetarea. Nu execută failback automat. Observația este despre accesibilitate; un firewall greșit poate arăta ca o avarie regională și poate produce o promovare inutilă.

Aurora administrează schimbarea writerului și protecții pentru global endpoint. Totuși, AWS documentează riscul de split-brain la failover neplanificat. Această implementare nu are o autoritate independentă de quorum pentru fencing strict. Nu se pretinde siguranță la orice partiție arbitrară. Conexiunile nu sunt păstrate între invocări; endpoint-ul se rezolvă din nou la conectare. Într-un incident cu regiunea veche încă accesibilă, operatorul oprește aplicațiile și publisherii vechi, confirmă noul writer și restabilește traficul conform runbook-ului.

Trei observații la un minut oferă un buget de detecție de aproximativ 2–3 minute, la care se adaugă promovarea, DNS și reconectarea. Nu constituie o garanție de RTO <5 minute; testul are pragul respectiv și poate eșua.

## RPO și recuperare

Replicarea Aurora este asincronă. Un failover forțat folosește explicit `AllowDataLoss=True`, deoarece sursa indisponibilă nu poate fi sincronizată. Obiectivul operațional de lag ≤1s se verifică prin `AuroraGlobalDBRPOLag`, reconciliere și timestamps; parametrul `rds.global_db_rpo` are minim 20s și este configurat la acel minim. La lag prea mare poate bloca commit-uri.

Backup-ul, outbox-ul și S3 replay sunt instrumente de recuperare; arhiva nu este actualizată sincron cu acceptarea. Dacă un commit se pierde înainte de replicare și înainte de arhivare, serverul nu îl poate recupera din aceste copii. Manifestul clientului permite resubmit pentru intențiile originale, inclusiv rezultatele HTTP incerte. Raportează separat datele recuperate prin resubmit și starea imediată după failover. Nu eticheta această recuperare ca RPO zero nativ.

Switchover-ul planificat sincronizează datele. Experimentul DB isolation întrerupe accesul SQL, păstrând replicarea storage; poate demonstra zero pierderi într-un test controlat. Nu demonstrează că un dezastru fizic cu replicare întreruptă nu ar pierde commit-uri.

## Securitate și costuri

DB private, fără IP public; fără NAT, cu endpoint-uri VPC pentru SQS/Secrets/S3. TLS DB `verify-full` cu bundle RDS; S3 și state blochează accesul public și transportul nesigur. Aurora folosește CMK-uri regionale cu rotație. IAM al funcțiilor limitează accesul SQS/S3/Secrets la resursele proprii, iar supervisorul are numai operațiile necesare promovării.

Pentru simplitatea laboratorului, funcțiile SQL folosesc același utilizator de administrare, iar API-ul folosește un token partajat. În producție sunt necesare roluri DB distincte și grant-uri minime, autentificare per client, rotația coordonată a secretelor, politici de acces la arhivă și notificări pentru alarme. Deployment-ul nu include conectarea alarmelor la destinatari. Acestea sunt limite explicite, nu condiții validate.

Costuri dominante: 4 instanțe Aurora, I/O și storage replicat, endpoint-uri VPC în fiecare AZ, Lambda, request-uri S3/SQS, snapshot-uri și transfer între regiuni. Capacitatea de 1.000 evenimente/s depinde de DB class, quota Lambda, batching și latența WAN. Ajustează după datele măsurate; nu deduce capacitatea AWS din benchmarkul SQLite.
