Real REMA 1000 answers, captured 2026-11-04 from the open API that shop.rema1000.dk uses
(`https://api.digital.rema1000.dk/api`). No login, no personal data. Long text fields
(declarations, nutrition, images, store information) are cut to keep the files small.

- `search_hakkede_tomater.json`: `GET /search/products?query=hakkede tomater&per_page=4`
- `products_page.json`: 4 campaign products and 2 normal ones from
  `GET /v3/products?per_page=1000&page=1`, with `meta.pagination` set to one page.
  Campaigns run 2026-11-01 to 2026-11-07; the normal price starts 2026-11-08.
- `recipes_lasagne.json`: the first hit of `GET /search/recipes?query=lasagne`, without the
  cooking steps, image and product labels.

Prices carry next week's price too (`starting_at` in the future). The adapter picks the one
that counts today, so tests fix today at 2026-11-04.
