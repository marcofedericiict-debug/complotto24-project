# Complotto24

Repository sorgente e registro di versione del progetto Complotto24.

Release tecnica corrente: **v0.4.2.2 PREFLIGHT-HARDENING-HOTFIX — 17/08/2026**.

Baseline editoriale: 70 articoli nel dataset JSON, più 6 seed PHP storici. Le bonifiche attive includono MEDIA-HOTFIX, IMAGE-QUALITY-HOTFIX, LEGAL-EDITORIAL-HOTFIX, FULL IMAGE REMEDIATION e FEATURED-IMAGE-EMERGENCY-HOTFIX.

Hotfix 0.4.2.2: aggiunto preflight bloccante nella pagina Complotto24 Sync per impedire pubblicazioni con JSON non valido, slug duplicati, permalink plain, mapping post→image incompleto, immagini mancanti/non decodificabili/sotto 1600x900 o asset superseded duplicati. Rimossi i 10 vecchi asset `batch042-XX.jpg`, byte-identici e non referenziati rispetto alle versioni `batch042-XX-featured-v2.jpg` attive.

La pipeline featured-image preserva `wp_generate_attachment_metadata()`, `wp_update_attachment_metadata()`, `set_post_thumbnail()`, fallback `_thumbnail_id` e URL hash-based. Il gate di revisione editoriale umana resta obbligatorio.

ZIP completo persistente: `/Complotto24/Builds/complotto24-core-v0.4.2.2-PREFLIGHT-HARDENING-HOTFIX.zip`

SHA-256: `bc8cdae72ed083a0e1071cf509c4670c920c315cf72dc6f75f8e8d9a50ce5360`

Manifest release: `releases/v0.4.2.2-PREFLIGHT-HARDENING-HOTFIX.md`

Baseline prevista per il batch 18/08/2026: 70 articoli → 80 dopo l'aggiunta dei 10 nuovi contenuti, con preflight automatico alle 09:00 e batch alle 10:00 Europe/Rome.
