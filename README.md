# Stock Finder globale

Scanner quantitativo che esplora una proxy ampia del FTSE All-World e restituisce fino a cinque titoli che rispettano **tutti** questi filtri:

1. prezzo in discesa da almeno tre mesi;
2. free cash flow TTM positivo e in crescita;
3. utile netto TTM positivo;
4. divergenza rialzista OBV sul timeframe 3D;
5. market cap almeno pari a 10 miliardi USD (configurabile);
6. valutazione confermata da almeno due metodi: la sottovalutazione passa
   sempre, mentre è ammessa una sopravvalutazione massima del 15%.

Se meno di cinque società superano le soglie, il report mostra meno di cinque nomi: il programma non allenta i filtri per riempire la classifica.

## Universo

Di default il programma scarica le partecipazioni azionarie correnti del **Vanguard FTSE All-World UCITS ETF (portafoglio 9679)**. È una proxy ampia e riproducibile dell'indice FTSE All-World: l'elenco ufficiale completo dell'indice è un dataset FTSE Russell con licenza, mentre l'ETF usa campionamento rappresentativo.

È possibile passare un CSV personalizzato con le colonne `yahoo_symbol`, `name`, `country`, `sector`, `weight_pct`. Se manca `yahoo_symbol`, il programma prova a costruirlo da `ticker` + `country`.

## Divergenza OBV 3D

Le barre 3D sono finestre di **tre giorni di calendario**, ancorate all'epoch Unix, coerenti con il riferimento CRM del 26 giugno 2026. Il filtro passa in uno dei due casi:

- la pendenza OBV sulle ultime 20 barre 3D è positiva mentre la pendenza del prezzo è negativa; oppure
- nelle ultime tre barre si verifica una swing divergence confermata: nuovo minimo del prezzo, minimo crescente dell'OBV e successiva conferma di prezzo e OBV.

Il secondo caso evita che una regressione lunga nasconda un'accumulazione recente e netta.

## Valutazione

Il programma calcola tre fair value nella stessa unità del prezzo quotato:

| Metodo | Implementazione |
|---|---|
| DCF (FCFE) | FCF TTM proiettato per 5 anni, crescita normalizzata e limitata al 12%, decadimento verso il 2,5%, costo dell'equity 10%. Poiché `OCF − CapEx` è trattato come cash flow per l'equity, il debito non viene sottratto una seconda volta. Il metodo è valido solo se almeno 2 degli ultimi 3 FCF annuali sono positivi. |
| Peter Lynch | Utile TTM × fair P/E; il fair P/E deriva dalla crescita EPS annuale mediana ed è limitato a 5–20. Non applicabile con crescita EPS non positiva. |
| EV / Sales | Mediana dell'EV/Sales storico della stessa società, applicata ai ricavi TTM; dal valore d'impresa implicito vengono sottratti debito e aggiunta cassa. |

La **media aritmetica** è esportata per confronto con il riferimento mostrato dall'utente; la **mediana**, più resistente agli outlier, viene usata nel report e nello scoring. Per il filtro rigido ogni metodo viene invece verificato separatamente con questa regola:

```text
prezzo / fair value − 1 ≤ 15%
```

Un metodo passa sempre quando il titolo è sottovalutato (`prezzo ≤ fair value`). Se il prezzo è superiore al fair value, passa soltanto fino al 15% di sopravvalutazione: per esempio, con fair value 100 sono ammessi prezzi fino a 115. Un titolo entra nel ranking solo se:

- sono disponibili almeno 2 metodi;
- almeno 2 metodi rispettano la tolleranza del 15%;
- la market cap convertita in USD supera la soglia.

Gli importi sono convertiti in USD con cambi Yahoo correnti. Il rapporto tra fair equity value e market cap viene poi applicato al prezzo della quotazione: questo riduce gli errori dovuti ad ADR, classi azionarie e prezzi espressi in centesimi.

## Scoring

I sei filtri sono rigidi e vengono applicati prima del ranking. I segnali delle sole finaliste diventano percentili:

| Blocco | Peso | Segnale |
|---|---:|---|
| Discesa | 20% | rendimento 3 mesi e pendenza logaritmica a 63 sedute |
| Crescita FCF | 25% | FCF TTM corrente vs finestra TTM precedente |
| Redditività | 15% | margine netto TTM e trimestri profittevoli |
| Divergenza OBV | 20% | pendenza OBV o swing divergence confermata su barre 3D |
| Valutazione | 20% | upside del fair value mediano e quota di metodi entro tolleranza |

La dimensione resta un filtro e non riceve punti: in questo modo le mega-cap non vengono favorite solo perché più grandi.

## Calibrazione CRM · 26 giugno 2026

Il caso Salesforce è riprodotto senza look-ahead sui dati disponibili a quella data:

- close 158,37 USD; rendimento 3 mesi circa −14,7%;
- swing 3D: low 146,32 sotto 149,80, ma OBV più alto di 41,22 milioni; conferma al 26 giugno;
- FCF TTM 14,661 miliardi USD contro 14,402 miliardi; utile TTM circa 8,023 miliardi;
- market cap stimata circa 129,7 miliardi USD.

Fonti primarie: [Salesforce FY26](https://www.sec.gov/Archives/edgar/data/1108524/000110852426000056/crm-q4fy26xexhibit991.htm), [Salesforce Q1 FY27](https://www.sec.gov/Archives/edgar/data/1108524/000110852426000125/crm-q1fy27xexhibit991.htm), [10-Q al 30 aprile 2026](https://www.sec.gov/Archives/edgar/data/1108524/000110852426000127/crm-20260430.htm). Il test automatico include la sequenza delle ultime barre 3D usata per la calibrazione.

## Installazione

Richiede Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Esecuzione

Scansione completa e report:

```bash
python -m stock_finder scan --output results --top 5
```

Tolleranza di sopravvalutazione più severa (la market cap minima resta $10B):

```bash
python -m stock_finder scan --max-overvaluation 0.10
```

Test rapido sui primi 300 titoli:

```bash
python -m stock_finder scan --output results-quick --max-symbols 300
```

Universo custom:

```bash
python -m stock_finder scan --universe mio_universo.csv --output results
```

Output principali:

- `top5.html`: dashboard leggibile nel browser;
- `top5.json`: risultati machine-readable;
- `all_candidates.csv`: finaliste e metriche complete;
- `diagnostics.json`: copertura, soglie ed errori dati;
- `universe.csv`: snapshot dell'universo usato.

Prezzi, fondamentali e cambi hanno cache versionate. Un run interrotto può riprendere dal pre-filtro dell'universo.

## Limiti

- Gli endpoint pubblici Yahoo non sono un feed istituzionale e possono avere buchi, ritardi, limiti di frequenza o mapping ticker imperfetti.
- DCF e fair value dipendono fortemente da crescita, tasso di sconto, multiplo storico e valuta. Sono stime, non valori osservabili.
- L'EV/Sales storico può incorporare un vecchio premio che il mercato non riconoscerà più.
- FCF è poco informativo per banche e assicurazioni; verificare sempre il settore e i filing.
- Una divergenza OBV segnala accumulazione relativa ma non garantisce un'inversione.
- Questo è screening quantitativo, non consulenza o raccomandazione finanziaria.
