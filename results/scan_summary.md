# Stock Finder — run del 23 agosto 2026

## Risultato con market cap minima $10B

| Rank | Simbolo | Società | Score | 3 mesi | FCF YoY | Market cap USD | DCF | Lynch | EV/Sales | Fair value consensus | FV vs prezzo | Metodi entro soglia |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 1 | VICI | VICI Properties Inc | 100,0 | −7,0% | +3,5% | $29,19B | 35,03 | 12,56 | 33,09 | 33,09 | +24,8% | 2/3 |

Con il filtro rigido da **$10 miliardi**, soltanto VICI supera contemporaneamente trend negativo, divergenza OBV 3D, FCF TTM crescente, utile positivo e valutazione multi-metodo entro tolleranza. Il programma non abbassa le soglie per riempire artificialmente la top 5.

## Regola di valutazione

- **DCF FCFE:** FCF TTM, crescita normalizzata, 5 anni, costo dell'equity 10%, terminal growth 2,5%; richiede almeno 2 degli ultimi 3 FCF annuali positivi.
- **Peter Lynch:** utile TTM moltiplicato per un fair P/E derivato dalla crescita EPS, limitato a 5–20.
- **EV/Sales:** mediana storica aziendale applicata ai ricavi TTM, con aggiustamento per cassa e debito.
- Servono almeno due modelli validi e due conferme. Ogni metodo conferma sempre se `prezzo ≤ fair value`; se il prezzo è superiore, conferma solo quando `prezzo / fair value − 1 ≤ 15%`.

VICI è confermata da DCF ed EV/Sales, entrambi sopra il prezzo. Peter Lynch stima invece 12,56: il prezzo è oltre il 15% sopra questa stima e il metodo non conferma. Essendo un REIT, la due diligence deve includere anche NAV, FFO/AFFO, costo del debito e scadenze.

## Calibrazione CRM al 26 giugno 2026

Il fixture Yahoo incluso nei test riproduce Salesforce al 26 giugno:

- close $158,37; rendimento 3 mesi −14,69%; trend 63 sedute negativo;
- barra 3D al 23 giugno con low $146,32 sotto $149,80, ma OBV maggiore di 41,22 milioni;
- conferma al 26 giugno con close e OBV in aumento;
- FCF TTM $14,661B contro $14,402B, utile TTM circa $8,023B e market cap stimata circa $129,7B.

CRM passa quindi il caso tecnico/qualitativo storico con la nuova clausola swing e supera ampiamente il filtro dimensionale. Non è automaticamente un titolo della classifica corrente: il ranking usa i dati più recenti del run.

Lo screenshot allegato sembra riferirsi alla vecchia #1 `300751.SZ`: il prezzo 193,66 coincide, mentre CRM il 26 giugno chiuse a 158,37. I gate attuali la escludono; inoltre la sua market cap, circa $8,05B, è sotto la soglia richiesta di $10B.

## Copertura

- Universo proxy Vanguard FTSE All-World: **3.787** azioni.
- Serie prezzo batch analizzabili: **2.302**.
- Downtrend al pre-filtro: **818**.
- Serie OHLCV complete: **460**.
- Divergenze prezzo/OBV valide: **87**.
- Società con FCF crescente e utile positivo: **17**.
- Almeno $10B: **10**; valutazione entro soglia: **1**.

La copertura non è 100%: gli endpoint pubblici Yahoo hanno restituito errori o rate limit per vari simboli. I dettagli completi sono in `diagnostics.json`; i risultati non imputano valori mancanti e falliscono in modo conservativo.

Screening quantitativo, non raccomandazione finanziaria. Verificare filing originali, liquidità, valuta, corporate action e sensibilità dei fair value.
