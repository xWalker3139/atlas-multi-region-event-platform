# Postmortem — [incident / experiment AWS]

Status: [draft / reviewed]. Data UTC: [ ]. Responsabil: [ ]. Run ID: [ ].

## Impact

- Interval indisponibilitate: [start — end UTC].
- Comenzi oferite / confirmate / incerte: [ ].
- Comenzi confirmate lipsă înainte de replay: [IDs + count].
- Efecte duplicate / sume greșite: [ ].
- RTO măsurat și criteriul de readiness: [ ].
- RPO observat, metoda și rezoluția: [ ].
- Throughput efecte unice, durată, backlog: [ ].
- Restore / replay / resubmit: [ce s-a recuperat, după verdictul inițial].

## Cronologie UTC

| Timp | Observație / acțiune | Dovadă |
|---|---|---|
| [ ] | Fault injectat / incident început | [ ] |
| [ ] | Prima detecție | [ ] |
| [ ] | Promovare solicitată | [ ] |
| [ ] | Writer nou disponibil | [ ] |
| [ ] | Scrieri stabile ≥30s | [ ] |
| [ ] | Backlog gol și reconciliere | [ ] |
| [ ] | Fault restaurat | [ ] |

## Cauză

[Trigger, cauza tehnică, factori care au amplificat impactul, alternative eliminate prin probe.]

## Ce a funcționat / ce a eșuat

[Detecție, DNS, idempotency, outbox, backup, rollback. Include false positives și limitele măsurătorilor.]

## Acțiuni

| Acțiune concretă | Owner | Termen | Verificare |
|---|---|---|---|
| [ ] | [ ] | [ ] | [ ] |

## Dovezi și verdict

[Receipts, intentions, rapoarte, RDS events, logs, lag metrics, backup/restore audit, plan TF.]

Verdict per prag: throughput [PASS/FAIL/not measured], zero-loss [ ], RTO [ ], RPO [ ], duplicates [ ], restore [ ].
