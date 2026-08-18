# Complotto24

Repository sorgente e registro di versione del progetto Complotto24.

Release tecnica corrente: **v0.5.0 INCREMENTAL IMPORTER — 18/08/2026**.

## Nuova architettura storage-safe

Il Core non contiene più il dataset storico né le immagini storiche. Gli articoli e gli allegati già pubblicati restano nel database e nella Media Library di WordPress e non vengono riscritti durante gli aggiornamenti del Core.

I nuovi contenuti vengono distribuiti come batch incrementali: `c24-batch.json` + directory `images/` contenente esclusivamente le immagini dei nuovi articoli. Il batch viene importato da **Strumenti > Complotto24 Batch Import**.

Protezione duplicati: gli slug già esistenti vengono saltati e ogni `batch_id` può essere importato una sola volta. I file temporanei del batch vengono eliminati dopo l'import. Per i nuovi media Complotto24 vengono mantenuti l'originale e soltanto le size WordPress `thumbnail` e `medium`, riducendo l'occupazione rispetto alla generazione indiscriminata di tutte le size registrate.

La migrazione dalla serie 0.4.x non elimina post, metadati o allegati esistenti.

ZIP Core: `/Complotto24/Builds/complotto24-core-v0.5.0-INCREMENTAL.zip`

SHA-256: `8b0f93ad6cc802d2da8ff7116c243d027a4bb1dc5d857fe8c4959c2f89b73e4b`
