# Domain Glossary

- `Root domain`: Registrable domain inferred from starting host (example: `example.com`).
- `Allowed URL`: URL with `http/https` and host equal to root domain or any subdomain.
- `Discovered URL`: Internal URL extracted from crawled pages.
- `Visited URL`: Discovered URL that was actually fetched by Scrapy.
- `Discovery source`: How a URL was found, such as anchor link, form action, non-anchor attribute reference, embedded route string, AI guess, or the original seed input.
- `Sitemap`: JSON artifact with URL list, per-page inbound/outbound counts, and internal link graph.
